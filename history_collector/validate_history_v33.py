import argparse
import asyncio
import csv
import json
import random
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

OUT = Path("data/history/v33_quality")
LEAGUE_URL = "https://info.nowscore.com/cn/League/2025-2026/36.html"
TARGET_MATCHES = 10


def dt_from_text(s):
    if not s:
        return None
    s = s.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def infer_change_time(raw, kickoff):
    raw = " ".join(raw.split())
    m = re.search(r"(?:(20\d{2})[-/])?(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", raw)
    if not m:
        return None
    year = int(m.group(1)) if m.group(1) else kickoff.year
    dt = datetime(year, int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
    # Historical pages normally omit year. Handle season crossing safely.
    if not m.group(1) and dt > kickoff and (dt - kickoff).days > 180:
        dt = dt.replace(year=year - 1)
    return dt


def num(s):
    try:
        return float(str(s).strip())
    except Exception:
        return None


def parse_detail(html, match_id, company_id, kickoff, source_url):
    soup = BeautifulSoup(html, "lxml")
    rows = []
    current_market = None
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True)
        header = text[:250]
        if "和局" in header or ("主" in header and "客" in header and "变化" in header and "盘" not in header[:80]):
            market = "1X2"
        elif "大" in header and "小" in header and "盘" in header:
            market = "OU"
        elif "主" in header and "盘" in header and "客" in header:
            market = "AH"
        else:
            continue
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 5:
                continue
            joined = " ".join(cells)
            change_at = infer_change_time(joined, kickoff)
            if not change_at:
                continue
            status = cells[-1].strip() if cells else ""
            rec = {"match_id": str(match_id), "company_id": str(company_id), "market": market,
                   "change_at": change_at.strftime("%Y-%m-%d %H:%M:%S"), "status": status,
                   "source_url": source_url, "raw": " | ".join(cells), "home": "", "draw": "", "away": "", "line": "", "over": "", "under": ""}
            # V3.2 confirmed layout: time, score, market values..., change, status.
            # Locate time cell, then use following cells; tolerate extra score column.
            ti = next((i for i,c in enumerate(cells) if infer_change_time(c, kickoff)), None)
            if ti is None:
                continue
            vals = cells[ti+1:]
            if vals and re.fullmatch(r"\d{1,2}[-:]\d{1,2}", vals[0]):
                vals = vals[1:]
            if market == "AH" and len(vals) >= 3:
                rec["home"], rec["line"], rec["away"] = vals[0], vals[1], vals[2]
            elif market == "1X2" and len(vals) >= 3:
                rec["home"], rec["draw"], rec["away"] = vals[0], vals[1], vals[2]
            elif market == "OU" and len(vals) >= 3:
                rec["over"], rec["line"], rec["under"] = vals[0], vals[1], vals[2]
            rows.append(rec)
    return rows


def valid_row(r, kickoff):
    t = dt_from_text(r["change_at"])
    if not t or t >= kickoff or "滚" in r.get("status", ""):
        return False, "post_or_live"
    if r["market"] == "AH":
        h, a = num(r["home"]), num(r["away"])
        if h is None or a is None or not r["line"]:
            return False, "missing_ah"
        if not (0.01 <= h <= 5 and 0.01 <= a <= 5):
            return False, "range_ah"
    elif r["market"] == "1X2":
        h, d, a = num(r["home"]), num(r["draw"]), num(r["away"])
        if None in (h,d,a):
            return False, "missing_1x2"
        if not all(1.0 <= x <= 100 for x in (h,d,a)):
            return False, "range_1x2"
    return True, "ok"


def choose_snapshot(rows, kickoff, minutes=None):
    target = kickoff if minutes is None else kickoff.replace() - __import__('datetime').timedelta(minutes=minutes)
    eligible = [r for r in rows if dt_from_text(r["change_at"]) <= target]
    return max(eligible, key=lambda r: dt_from_text(r["change_at"])) if eligible else None


async def discover_matches(page):
    # Reuse proven round-switching idea: collect all ids visible after clicking each round selector.
    found = {}
    await page.goto(LEAGUE_URL, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(3000)
    for rnd in range(1,39):
        try:
            loc = page.get_by_text(str(rnd), exact=True)
            if await loc.count():
                await loc.first.click(timeout=5000)
                await page.wait_for_timeout(800)
        except Exception:
            pass
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            m = re.search(r"(?:analysis|odds|match|detail)[^0-9]{0,30}(\d{7,9})", href, re.I)
            if m:
                found[m.group(1)] = rnd
    return found


async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--companies", type=int, default=17)
    ap.add_argument("--wait", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=20250915)
    args=ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows=[]; reports=[]; snapshots=[]

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        context=await browser.new_context(locale="zh-CN", viewport={"width":1600,"height":1200})
        page=await context.new_page()
        discovered=await discover_matches(page)
        ids=list(discovered.keys())
        random.Random(args.seed).shuffle(ids)
        selected=ids[:args.count]
        print("V3.3 discovered",len(ids),"selected",selected)

        for idx,mid in enumerate(selected,1):
            main_url=f"https://live.nowscore.com/odds/match/{mid}.htm"
            print(f"[{idx}/{len(selected)}] {mid}")
            try:
                await page.goto(main_url,wait_until="domcontentloaded",timeout=90000); await page.wait_for_timeout(args.wait*1000)
                kickoff_raw=await page.locator("#hide_matchTime").get_attribute("value") if await page.locator("#hide_matchTime").count() else None
                kickoff=dt_from_text(kickoff_raw)
                hrefs=await page.locator('a[href*="3in1Odds.aspx"]').evaluate_all("els=>els.map(e=>e.getAttribute('href'))")
                details=[]
                for href in hrefs:
                    if not href: continue
                    full=urljoin(main_url,href)
                    m=re.search(r"companyid=(\d+).*?[?&]id=(\d+)",full,re.I)
                    if m and m.group(2)==mid and (m.group(1),full) not in details:
                        details.append((m.group(1),full))
                if not kickoff:
                    reports.append({"match_id":mid,"round":discovered[mid],"status":"FAIL","reason":"kickoff_missing"}); continue
                match_rows=[]
                for cid,url in details[:args.companies]:
                    dp=await context.new_page()
                    try:
                        rr=await dp.goto(url,wait_until="domcontentloaded",timeout=90000); await dp.wait_for_timeout(args.wait*1000)
                        if rr and rr.status==200:
                            parsed=parse_detail(await dp.content(),mid,cid,kickoff,url)
                            match_rows.extend(parsed); all_rows.extend(parsed)
                    except Exception as e:
                        print(" detail error",cid,repr(e))
                    finally:
                        await dp.close()

                valid=[]; abnormal=[]
                for r in match_rows:
                    ok,reason=valid_row(r,kickoff)
                    if ok: valid.append(r)
                    else:
                        x=dict(r); x["quality_reason"]=reason; abnormal.append(x)
                by_company=defaultdict(set)
                for r in valid:
                    if r["market"] in ("AH","1X2"): by_company[r["company_id"]].add(r["market"])
                ah_comp={c for c,m in by_company.items() if "AH" in m}
                euro_comp={c for c,m in by_company.items() if "1X2" in m}
                both=ah_comp & euro_comp
                contamination=sum(1 for r in valid if dt_from_text(r["change_at"]) >= kickoff or "滚" in r.get("status",""))
                reasons=[]
                if len(ah_comp)<3: reasons.append("AH公司不足3")
                if len(euro_comp)<3: reasons.append("1X2公司不足3")
                if len(both)<3: reasons.append("AH+1X2共同公司不足3")
                if contamination: reasons.append("CLOSE候选存在赛后/滚球污染")
                status="PASS" if not reasons else "FAIL"
                if status=="PASS" and abnormal: status="WARN"

                for cid in sorted(both, key=lambda x:int(x)):
                    for market in ("AH","1X2"):
                        rs=[r for r in valid if r["company_id"]==cid and r["market"]==market]
                        rs.sort(key=lambda r:dt_from_text(r["change_at"]))
                        for label,mins in (("OPEN","OPEN"),("T180",180),("T60",60),("T30",30),("CLOSE",None)):
                            s=rs[0] if mins=="OPEN" and rs else choose_snapshot(rs,kickoff,mins)
                            if s:
                                z=dict(s); z["snapshot_type"]=label; z["kickoff"]=kickoff.strftime("%Y-%m-%d %H:%M:%S"); snapshots.append(z)
                reports.append({"match_id":mid,"round":discovered[mid],"kickoff":kickoff_raw,"status":status,
                                "detail_companies":len(details),"ah_companies":len(ah_comp),"euro_companies":len(euro_comp),
                                "both_companies":len(both),"valid_rows":len(valid),"abnormal_rows":len(abnormal),
                                "contamination":contamination,"reason":";".join(reasons)})
                print(" ",status,"AH",len(ah_comp),"1X2",len(euro_comp),"BOTH",len(both),"valid",len(valid),"abnormal",len(abnormal))
            except Exception as e:
                reports.append({"match_id":mid,"round":discovered.get(mid),"status":"FAIL","reason":repr(e)})

        await browser.close()

    def write_csv(path,rows):
        if not rows: return
        keys=[]
        for r in rows:
            for k in r:
                if k not in keys: keys.append(k)
        with path.open("w",newline="",encoding="utf-8-sig") as f:
            w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)
    write_csv(OUT/"quality_report.csv",reports)
    write_csv(OUT/"odds_timeline.csv",all_rows)
    write_csv(OUT/"odds_snapshots.csv",snapshots)
    (OUT/"quality_report.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding="utf-8")
    pcount=sum(r.get("status")=="PASS" for r in reports); wcount=sum(r.get("status")=="WARN" for r in reports); fcount=sum(r.get("status")=="FAIL" for r in reports)
    contaminated=sum(int(r.get("contamination",0) or 0) for r in reports)
    print("="*78); print("V3.3 数据质量门禁 + 10场交叉验证")
    print("PASS",pcount,"WARN",wcount,"FAIL",fcount,"TOTAL",len(reports)); print("赛前数据污染记录",contaminated)
    print("门禁建议: PASS+WARN>=9/10 且 contamination=0，才进入英超380场。")
    for r in reports: print(r.get("match_id"),"R",r.get("round"),r.get("status"),"AH",r.get("ah_companies"),"1X2",r.get("euro_companies"),"BOTH",r.get("both_companies"),r.get("reason",""))
    print("输出:",OUT); print("="*78)

if __name__=="__main__": asyncio.run(main())
