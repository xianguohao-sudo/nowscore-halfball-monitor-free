import argparse
import asyncio
import csv
import json
from pathlib import Path

from history_collector.validate_history_v33 import discover_matches, parse_detail_html, parse_kickoff
from playwright.async_api import async_playwright
from urllib.parse import urljoin
import re

OUT = Path("data/history/v4_epl_2025_26")


def parse_saved_time(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            from datetime import datetime
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            pass
    return None


def write_csv(path, rows):
    if not rows:
        return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)


async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--start",type=int,default=0)
    ap.add_argument("--count",type=int,default=20)
    ap.add_argument("--companies",type=int,default=17)
    ap.add_argument("--wait",type=float,default=0.5)
    args=ap.parse_args(); OUT.mkdir(parents=True,exist_ok=True)
    reports=[]; timeline=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="zh-CN",viewport={"width":1600,"height":1200})
        page=await ctx.new_page()
        found=await discover_matches(page)
        ordered=sorted(found.items(),key=lambda x:(x[1],int(x[0])))
        batch=ordered[args.start:args.start+args.count]
        print("V4 discovered",len(ordered),"start",args.start,"count",len(batch))
        for n,(mid,rnd) in enumerate(batch,1):
            print(f"[{n}/{len(batch)}] R{rnd} {mid}")
            try:
                main=f"https://live.nowscore.com/odds/match/{mid}.htm"
                await page.goto(main,wait_until="domcontentloaded",timeout=90000)
                await page.wait_for_timeout(args.wait*1000)
                raw=await page.locator("#hide_matchTime").get_attribute("value") if await page.locator("#hide_matchTime").count() else None
                kickoff=parse_kickoff(raw)
                if not kickoff: raise RuntimeError("kickoff_missing")
                hrefs=await page.locator('a[href*="3in1Odds.aspx"]').evaluate_all("els=>els.map(e=>e.getAttribute('href'))")
                details=[]
                for href in hrefs:
                    if not href: continue
                    u=urljoin(main,href); m=re.search(r"companyid=(\d+).*?[?&]id=(\d+)",u,re.I)
                    if m and m.group(2)==mid and m.group(1) not in [x[0] for x in details]: details.append((m.group(1),u))
                rows=[]
                for cid,u in details[:args.companies]:
                    dp=await ctx.new_page()
                    try:
                        rr=await dp.goto(u,wait_until="domcontentloaded",timeout=90000)
                        await dp.wait_for_timeout(args.wait*1000)
                        if rr and rr.status==200:
                            rows.extend(parse_detail_html(await dp.content(),mid,cid,kickoff,u))
                    except Exception as e:
                        print(" detail error",cid,repr(e))
                    finally:
                        await dp.close()
                timeline.extend(rows)
                usable=[x for x in rows if x.get("usable")]
                ah={x["company_id"] for x in usable if x["market"]=="AH"}
                eu={x["company_id"] for x in usable if x["market"]=="1X2"}
                both=ah & eu
                contamination=sum(1 for x in usable if parse_saved_time(x["change_time"])>=kickoff or x.get("status")=="滚")
                status="PASS" if len(ah)>=3 and len(eu)>=3 and len(both)>=3 and contamination==0 else "FAIL"
                reports.append({"match_id":mid,"round":rnd,"kickoff":raw,"status":status,"detail_companies":len(details),"ah_companies":len(ah),"euro_companies":len(eu),"both_companies":len(both),"timeline_rows":len(rows),"usable_rows":len(usable),"contamination":contamination})
                print(status,"AH",len(ah),"1X2",len(eu),"BOTH",len(both),"usable",len(usable),"contamination",contamination)
            except Exception as e:
                reports.append({"match_id":mid,"round":rnd,"status":"FAIL","error":repr(e)})
                print("FAIL",repr(e))
        await browser.close()
    end=args.start+len(batch)-1
    tag=f"{args.start:03d}_{end:03d}" if batch else f"{args.start:03d}_empty"
    write_csv(OUT/f"matches_{tag}.csv",reports)
    write_csv(OUT/f"timeline_{tag}.csv",timeline)
    (OUT/f"report_{tag}.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding="utf-8")
    ps=sum(r.get("status")=="PASS" for r in reports)
    contamination=sum(int(r.get("contamination",0) or 0) for r in reports)
    print("="*70)
    print("V4 EPL batch",tag,"PASS",ps,"FAIL",len(reports)-ps,"TOTAL",len(reports),"contamination",contamination)
    print("timeline rows",len(timeline))
    print("="*70)

if __name__=="__main__": asyncio.run(main())
