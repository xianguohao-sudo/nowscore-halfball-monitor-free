import argparse
import asyncio
import csv
import re
from pathlib import Path
from playwright.async_api import async_playwright


def read_csv(path):
    with open(path,encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))

def write_csv(path,rows):
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys:keys.append(k)
    with open(path,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def score_from_text(text):
    # Prefer explicit full-time markers; fall back to plausible score pairs.
    pats=[r'(?:全场|完场|FT)[^0-9]{0,20}(\d{1,2})\s*[-:]\s*(\d{1,2})',r'(\d{1,2})\s*[-:]\s*(\d{1,2})']
    for pat in pats:
        for m in re.finditer(pat,text,re.I):
            h,a=int(m.group(1)),int(m.group(2))
            if 0<=h<=15 and 0<=a<=15:return h,a
    return None

async def main():
    ap=argparse.ArgumentParser();ap.add_argument('--matches',required=True);ap.add_argument('--out',default='data/history/v51_results/matches_380_results.csv');ap.add_argument('--wait',type=float,default=0.15);args=ap.parse_args()
    rows=read_csv(args.matches);out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    ok=0;failed=[]
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True);page=await b.new_page(locale='zh-CN',viewport={'width':1400,'height':1000})
        for i,r in enumerate(rows,1):
            mid=r.get('match_id','');url=f'https://live.nowscore.com/odds/match/{mid}.htm'
            try:
                await page.goto(url,wait_until='domcontentloaded',timeout=60000);await page.wait_for_timeout(args.wait*1000)
                text=await page.locator('body').inner_text();sc=score_from_text(text)
                if sc:
                    h,a=sc;r['home_goals']=h;r['away_goals']=a;r['result']='H' if h>a else 'D' if h==a else 'A';r['result_source']='match_page';ok+=1
                else:failed.append(mid)
            except Exception as e:
                failed.append(mid);print('FAIL',mid,repr(e))
            if i%20==0:print('results',i,'/',len(rows),'ok',ok,'missing',len(failed))
        await b.close()
    write_csv(out,rows)
    print('='*78);print('V5.1 赛果补齐');print('matches',len(rows),'results',ok,'missing',len(failed));print('missing_ids',failed);print('output',out);print('='*78)
    if ok!=len(rows):raise SystemExit(f'RESULT ENRICH FAILED: expected {len(rows)} got {ok}')

if __name__=='__main__':asyncio.run(main())
