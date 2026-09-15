import argparse
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

OUT = Path("data/history/v3_probe")
KEYWORDS = ("odd", "odds", "handicap", "asian", "europe", "1x2", "change", "history", "detail", "analysis", "ajax", "3in1")
DETAIL_RE = re.compile(r"/odds/3in1Odds\.aspx\?companyid=(\d+)&(?:amp;)?id=(\d+)", re.I)


def interesting(url: str) -> bool:
    u = url.lower()
    return "nowscore" in u and any(k in u for k in KEYWORDS)


def safe_name(url: str, idx: int) -> str:
    p = urlparse(url)
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", p.path.strip("/") or "root")[-90:]
    return f"{idx:03d}_{base}.txt"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match-id", default="2789129")
    ap.add_argument("--wait", type=int, default=5)
    ap.add_argument("--companies", type=int, default=5, help="穿透前N家有详页的公司")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    events, bodies, reports = [], [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(locale="zh-CN", viewport={"width": 1600, "height": 1200})
        page = await context.new_page()

        def on_request(req):
            if interesting(req.url):
                events.append({"kind":"request","method":req.method,"resource_type":req.resource_type,
                               "url":req.url,"post_data":req.post_data})

        async def capture_response(resp):
            if not interesting(resp.url): return
            rec={"kind":"response","status":resp.status,"url":resp.url,
                 "content_type":resp.headers.get("content-type","")}
            events.append(rec)
            try:
                ct=rec["content_type"].lower()
                if "text" in ct or "json" in ct or "javascript" in ct:
                    text=await resp.text()
                    if text: bodies.append((resp.url,text[:1500000]))
            except Exception as e: rec["body_error"]=repr(e)

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(capture_response(r)))

        main_url=f"https://live.nowscore.com/odds/match/{args.match_id}.htm"
        print("OPEN MAIN:",main_url)
        resp=await page.goto(main_url,wait_until="domcontentloaded",timeout=90000)
        await page.wait_for_timeout(args.wait*1000)
        html=await page.content()
        (OUT/"main.html").write_text(html,encoding="utf-8")
        kickoff=await page.locator("#hide_matchTime").get_attribute("value") if await page.locator("#hide_matchTime").count() else None

        # 关键发现：三合一主表每家公司“详”直接指向 3in1Odds.aspx?companyid=X&id=match。
        hrefs=await page.locator('a[href*="3in1Odds.aspx"]').evaluate_all("els => els.map(e => e.getAttribute('href'))")
        details=[]
        for href in hrefs:
            if not href: continue
            full=urljoin(main_url,href)
            m=re.search(r"companyid=(\d+).*?[?&]id=(\d+)",full,re.I)
            if m and m.group(2)==str(args.match_id):
                item={"company_id":m.group(1),"url":full}
                if item not in details: details.append(item)
        print("发现公司详页:",len(details),"开赛:",kickoff)

        for i,item in enumerate(details[:args.companies],1):
            dpage=await context.new_page()
            dpage.on("request", on_request)
            dpage.on("response", lambda r: asyncio.create_task(capture_response(r)))
            try:
                print(f"DETAIL {i}/{min(len(details),args.companies)} company={item['company_id']} {item['url']}")
                r=await dpage.goto(item["url"],wait_until="domcontentloaded",timeout=90000)
                await dpage.wait_for_timeout(args.wait*1000)
                dh=await dpage.content()
                text=await dpage.locator("body").inner_text()
                (OUT/f"detail_company_{item['company_id']}.html").write_text(dh,encoding="utf-8")
                (OUT/f"detail_company_{item['company_id']}.txt").write_text(text,encoding="utf-8")
                # 抽取页面中所有日期时间样式，判断详页是否直接携带历史时间轴。
                times=sorted(set(re.findall(r"(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+)?\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?",text)))
                reports.append({"company_id":item["company_id"],"url":item["url"],"status":r.status if r else None,
                                "html_len":len(dh),"text_len":len(text),"time_samples":times[:30]})
            except Exception as e:
                reports.append({"company_id":item["company_id"],"url":item["url"],"error":repr(e)})
            finally: await dpage.close()

        # JC页作为时间戳阳性对照：它已确认直接内嵌 changeTime JSON。
        jc=f"https://m.nowscore.com/Analy/JcOddsDetail?scheid={args.match_id}"
        jpage=await context.new_page()
        try:
            r=await jpage.goto(jc,wait_until="domcontentloaded",timeout=90000); await jpage.wait_for_timeout(2000)
            jh=await jpage.content(); (OUT/"jc_positive_control.html").write_text(jh,encoding="utf-8")
            changes=re.findall(r'"changeTime"\s*:\s*"([^"]+)"',jh)
            reports.append({"type":"jc_positive_control","url":jc,"status":r.status if r else None,"change_times":changes})
            print("JC阳性对照 changeTime:",len(changes))
        except Exception as e: reports.append({"type":"jc_positive_control","url":jc,"error":repr(e)})
        finally: await jpage.close()
        await page.wait_for_timeout(1500); await browser.close()

    seen,uniq=set(),[]
    for e in events:
        key=(e.get("kind"),e.get("method"),e.get("status"),e.get("url"),e.get("post_data"))
        if key not in seen: seen.add(key); uniq.append(e)
    (OUT/"network.json").write_text(json.dumps(uniq,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"detail_report.json").write_text(json.dumps({"match_id":args.match_id,"kickoff":kickoff,"detail_count":len(details),"reports":reports},ensure_ascii=False,indent=2),encoding="utf-8")
    for i,(url,body) in enumerate(bodies,1):
        (OUT/safe_name(url,i)).write_text(f"URL: {url}\n\n{body}",encoding="utf-8")

    print("="*72)
    print("V3.1 NowScore 公司三合一历史详页穿透")
    print("Match ID:",args.match_id,"kickoff:",kickoff)
    print("发现公司详页:",len(details),"本次穿透:",min(len(details),args.companies))
    for r in reports:
        if r.get("company_id"):
            print("company",r["company_id"],"status",r.get("status"),"time_samples",len(r.get("time_samples",[])))
            for t in r.get("time_samples",[])[:8]: print("  TIME",t)
    print("JC阳性对照用于证明时间解析链路，不冒充博彩公司亚洲盘。")
    print("输出:",OUT/"detail_report.json")
    print("="*72)

if __name__=="__main__": asyncio.run(main())
