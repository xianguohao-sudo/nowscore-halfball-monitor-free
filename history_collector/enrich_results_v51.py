import argparse
import asyncio
import csv
import re
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

LEAGUE_URL='https://info.nowscore.com/cn/League/2025-2026/36.html'


def read_csv(path):
    with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def write_csv(path,rows):
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys:keys.append(k)
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def score_pair(text):
    # Score cell only: allow 0-0..15-15, reject dates/times/handicaps.
    m=re.fullmatch(r'\s*(\d{1,2})\s*[-:]\s*(\d{1,2})\s*',text or '')
    if not m:return None
    h,a=map(int,m.groups())
    return (h,a) if h<=15 and a<=15 else None

def extract_round_results(html, wanted):
    soup=BeautifulSoup(html,'lxml');found={}
    for tr in soup.find_all('tr'):
        anchors=tr.find_all('a',href=True);mid=None
        for a in anchors:
            href=a.get('href','')
            for candidate in wanted:
                if candidate in href:
                    mid=candidate;break
            if mid:break
        if not mid:continue
        cells=[x.get_text(' ',strip=True) for x in tr.find_all(['td','th'])]
        # League row format has a dedicated full-time score cell. Choose exact score-shaped
        # cells only; if multiple exist, the first is full-time and later one is half-time.
        pairs=[]
        for c in cells:
            sc=score_pair(c)
            if sc:pairs.append((c,sc))
        if pairs:
            found[mid]=(pairs[0][1],cells)
    return found

async def main():
    ap=argparse.ArgumentParser();ap.add_argument('--matches',required=True);ap.add_argument('--out',default='data/history/v51_results/matches_380_results.csv');ap.add_argument('--wait',type=float,default=0.8);args=ap.parse_args()
    rows=read_csv(args.matches);out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    byid={r.get('match_id',''):r for r in rows};wanted=set(byid);found={}
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True);page=await b.new_page(locale='zh-CN',viewport={'width':1600,'height':1200})
        await page.goto(LEAGUE_URL,wait_until='domcontentloaded',timeout=90000);await page.wait_for_timeout(2500)
        for rnd in range(1,39):
            try:
                loc=page.get_by_text(str(rnd),exact=True)
                if await loc.count():await loc.first.click(timeout=5000);await page.wait_for_timeout(args.wait*1000)
            except Exception as e:print('round click warning',rnd,repr(e))
            got=extract_round_results(await page.content(),wanted-set(found))
            for mid,(sc,cells) in got.items():
                h,a=sc;found[mid]=(h,a,rnd,cells)
            print('round',rnd,'new',len(got),'total_results',len(found))
        await b.close()
    for mid,(h,a,rnd,cells) in found.items():
        r=byid[mid];r['home_goals']=h;r['away_goals']=a;r['result']='H' if h>a else 'D' if h==a else 'A';r['result_source']='league_round_row';r['result_round']=rnd
    failed=sorted(wanted-set(found),key=lambda x:int(x))
    write_csv(out,rows)
    print('='*78);print('V5.1 赛果补齐 - league round source');print('matches',len(rows),'results',len(found),'missing',len(failed));print('missing_ids',failed);print('output',out);print('='*78)
    if len(found)!=len(rows):raise SystemExit(f'RESULT ENRICH FAILED: expected {len(rows)} got {len(found)}')

if __name__=='__main__':asyncio.run(main())
