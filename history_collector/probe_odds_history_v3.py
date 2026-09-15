import argparse
import asyncio
import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

OUT = Path("data/history/v3_probe")
KEYWORDS = ("odd", "odds", "handicap", "asian", "europe", "1x2", "change", "history", "detail", "analysis", "ajax", "3in1")


def interesting(url: str) -> bool:
    u = url.lower()
    return "nowscore" in u and any(k in u for k in KEYWORDS)


def safe_name(url: str, idx: int) -> str:
    p = urlparse(url)
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", p.path.strip("/") or "root")[-90:]
    return f"{idx:03d}_{base}.txt"


def fnum(v):
    try:
        return float(v)
    except Exception:
        return None


def parse_kickoff(value: str):
    if not value:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            pass
    return None


def parse_change_time(raw: str, kickoff: datetime):
    if not raw or not kickoff:
        return None
    s = " ".join(raw.split())
    # 3in1 历史表通常只有 MM-DD HH:MM；年份按比赛开赛年份推断。
    m = re.search(r"(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    month, day, hour, minute = map(int, m.groups())
    candidates = []
    for year in (kickoff.year - 1, kickoff.year, kickoff.year + 1):
        try:
            dt = datetime(year, month, day, hour, minute)
            candidates.append(dt)
        except ValueError:
            pass
    return min(candidates, key=lambda x: abs((x - kickoff).total_seconds())) if candidates else None


def market_from_header(cells):
    h = [x.replace(" ", "") for x in cells]
    if len(h) >= 7 and h[:7] == ["时", "比分", "主", "盘", "客", "变化", "状"]:
        return "AH"
    if len(h) >= 7 and h[:7] == ["时", "比分", "大", "盘", "小", "变化", "状"]:
        return "OU"
    if len(h) >= 7 and h[:7] == ["时", "比分", "主", "和局", "客", "变化", "状"]:
        return "1X2"
    return None


def parse_detail_html(html: str, match_id: str, company_id: str, kickoff: datetime):
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        header = [x.get_text(" ", strip=True) for x in trs[0].find_all(["th", "td"])]
        market = market_from_header(header)
        if not market:
            continue
        for tr in trs[1:]:
            c = [x.get_text(" ", strip=True) for x in tr.find_all(["th", "td"])]
            if len(c) < 7:
                continue
            dt = parse_change_time(c[5], kickoff)
            if not dt:
                continue
            status = c[6].strip()
            # “滚”明确为滚球；即/早属于赛前候选，但最终仍以 change_time < kickoff 硬过滤。
            pregame = dt < kickoff and status != "滚"
            base = {
                "match_id": str(match_id), "company_id": str(company_id), "market": market,
                "change_time": dt.strftime("%Y-%m-%d %H:%M"), "minute": c[0], "score": c[1],
                "status": status, "pregame": pregame,
                "home": "", "line": "", "draw": "", "away": "", "over": "", "under": ""
            }
            if market == "AH":
                base.update(home=c[2], line=c[3], away=c[4])
            elif market == "OU":
                base.update(over=c[2], line=c[3], under=c[4])
            else:
                base.update(home=c[2], draw=c[3], away=c[4])
            # 封盘空值仍保留原始时间轴，但不允许成为快照。
            base["usable"] = bool(status != "滚" and dt < kickoff and (
                (market == "AH" and fnum(c[2]) is not None and c[3] not in ("", "封") and fnum(c[4]) is not None) or
                (market == "OU" and fnum(c[2]) is not None and c[3] not in ("", "封") and fnum(c[4]) is not None) or
                (market == "1X2" and fnum(c[2]) is not None and fnum(c[3]) is not None and fnum(c[4]) is not None)
            ))
            rows.append(base)
    rows.sort(key=lambda r: (r["market"], r["change_time"]))
    return rows


def choose_at_or_before(rows, target):
    valid = [r for r in rows if r["usable"] and datetime.strptime(r["change_time"], "%Y-%m-%d %H:%M") <= target]
    return max(valid, key=lambda r: r["change_time"]) if valid else None


def build_snapshots(rows, kickoff):
    out = []
    for market in ("AH", "1X2", "OU"):
        mr = [r for r in rows if r["market"] == market]
        usable = [r for r in mr if r["usable"]]
        if not usable:
            continue
        first = min(usable, key=lambda r: r["change_time"])
        targets = [
            ("OPEN", None),
            ("T-180", kickoff - timedelta(minutes=180)),
            ("T-60", kickoff - timedelta(minutes=60)),
            ("T-30", kickoff - timedelta(minutes=30)),
            ("CLOSE", kickoff - timedelta(microseconds=1)),
        ]
        for label, target in targets:
            r = first if label == "OPEN" else choose_at_or_before(mr, target)
            if r:
                x = dict(r); x["snapshot"] = label; out.append(x)
    return out


def write_csv(path, rows):
    fields = ["match_id","company_id","market","change_time","minute","score","status","pregame","usable",
              "home","line","draw","away","over","under"]
    if rows and "snapshot" in rows[0]: fields.append("snapshot")
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--match-id", default="2789129")
    ap.add_argument("--wait", type=int, default=5)
    ap.add_argument("--companies", type=int, default=5)
    args=ap.parse_args(); OUT.mkdir(parents=True, exist_ok=True)
    events=[]; bodies=[]; reports=[]; all_rows=[]; all_snapshots=[]

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        context=await browser.new_context(locale="zh-CN",viewport={"width":1600,"height":1200})
        page=await context.new_page()
        def on_request(req):
            if interesting(req.url): events.append({"kind":"request","method":req.method,"resource_type":req.resource_type,"url":req.url,"post_data":req.post_data})
        async def capture_response(resp):
            if not interesting(resp.url): return
            rec={"kind":"response","status":resp.status,"url":resp.url,"content_type":resp.headers.get("content-type","")}; events.append(rec)
            try:
                ct=rec["content_type"].lower()
                if "text" in ct or "json" in ct or "javascript" in ct:
                    text=await resp.text()
                    if text: bodies.append((resp.url,text[:1500000]))
            except Exception as e: rec["body_error"]=repr(e)
        page.on("request",on_request); page.on("response",lambda r: asyncio.create_task(capture_response(r)))

        main_url=f"https://live.nowscore.com/odds/match/{args.match_id}.htm"
        print("OPEN MAIN:",main_url)
        resp=await page.goto(main_url,wait_until="domcontentloaded",timeout=90000); await page.wait_for_timeout(args.wait*1000)
        html=await page.content(); (OUT/"main.html").write_text(html,encoding="utf-8")
        kickoff_raw=await page.locator("#hide_matchTime").get_attribute("value") if await page.locator("#hide_matchTime").count() else None
        kickoff=parse_kickoff(kickoff_raw)
        if not kickoff: raise RuntimeError(f"无法解析开赛时间: {kickoff_raw!r}")
        hrefs=await page.locator('a[href*="3in1Odds.aspx"]').evaluate_all("els => els.map(e => e.getAttribute('href'))")
        details=[]
        for href in hrefs:
            if not href: continue
            full=urljoin(main_url,href); m=re.search(r"companyid=(\d+).*?[?&]id=(\d+)",full,re.I)
            if m and m.group(2)==str(args.match_id):
                item={"company_id":m.group(1),"url":full}
                if item not in details: details.append(item)
        print("发现公司详页:",len(details),"开赛:",kickoff_raw)

        for i,item in enumerate(details[:args.companies],1):
            dpage=await context.new_page(); dpage.on("request",on_request); dpage.on("response",lambda r: asyncio.create_task(capture_response(r)))
            try:
                print(f"DETAIL {i}/{min(len(details),args.companies)} company={item['company_id']} {item['url']}")
                r=await dpage.goto(item["url"],wait_until="domcontentloaded",timeout=90000); await dpage.wait_for_timeout(args.wait*1000)
                dh=await dpage.content(); text=await dpage.locator("body").inner_text()
                (OUT/f"detail_company_{item['company_id']}.html").write_text(dh,encoding="utf-8")
                (OUT/f"detail_company_{item['company_id']}.txt").write_text(text,encoding="utf-8")
                parsed=parse_detail_html(dh,args.match_id,item["company_id"],kickoff)
                snaps=build_snapshots(parsed,kickoff)
                all_rows.extend(parsed); all_snapshots.extend(snaps)
                counts={m:sum(1 for x in parsed if x["market"]==m) for m in ("AH","1X2","OU")}
                usable={m:sum(1 for x in parsed if x["market"]==m and x["usable"]) for m in ("AH","1X2","OU")}
                reports.append({"company_id":item["company_id"],"url":item["url"],"status":r.status if r else None,"rows":counts,"usable_pregame":usable,"snapshots":len(snaps)})
            except Exception as e: reports.append({"company_id":item["company_id"],"url":item["url"],"error":repr(e)})
            finally: await dpage.close()
        await browser.close()

    write_csv(OUT/"odds_timeline.csv",all_rows); write_csv(OUT/"odds_snapshots.csv",all_snapshots)
    (OUT/"odds_timeline.json").write_text(json.dumps(all_rows,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"odds_snapshots.json").write_text(json.dumps(all_snapshots,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"detail_report.json").write_text(json.dumps({"version":"V3.2","match_id":args.match_id,"kickoff":kickoff_raw,"detail_count":len(details),"reports":reports},ensure_ascii=False,indent=2),encoding="utf-8")
    seen=set(); uniq=[]
    for e in events:
        key=(e.get("kind"),e.get("method"),e.get("status"),e.get("url"),e.get("post_data"))
        if key not in seen: seen.add(key); uniq.append(e)
    (OUT/"network.json").write_text(json.dumps(uniq,ensure_ascii=False,indent=2),encoding="utf-8")
    for i,(url,body) in enumerate(bodies,1): (OUT/safe_name(url,i)).write_text(f"URL: {url}\n\n{body}",encoding="utf-8")

    print("="*78); print("V3.2 NowScore 历史赔率结构化解析")
    print("Match ID:",args.match_id,"kickoff:",kickoff_raw,"公司:",min(len(details),args.companies))
    print("原始历史行:",len(all_rows),"赛前可用行:",sum(1 for x in all_rows if x["usable"]),"快照:",len(all_snapshots))
    for r in reports:
        print("company",r.get("company_id"),"rows",r.get("rows"),"usable",r.get("usable_pregame"),"snapshots",r.get("snapshots"),r.get("error",''))
    print("\n=== CLOSE 快照 ===")
    for x in all_snapshots:
        if x.get("snapshot")=="CLOSE":
            vals = f"home={x['home']} line={x['line']} draw={x['draw']} away={x['away']} over={x['over']} under={x['under']}"
            print(f"company={x['company_id']} {x['market']} {x['change_time']} {vals}")
    print("输出:",OUT/"odds_timeline.csv",OUT/"odds_snapshots.csv"); print("="*78)

if __name__=="__main__": asyncio.run(main())
