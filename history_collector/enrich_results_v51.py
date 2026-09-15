import argparse
import asyncio
import csv
import re
import unicodedata
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

def norm(s):
    s=unicodedata.normalize('NFKC',str(s or '')).lower()
    return re.sub(r'[\s\-_.·•（）()\[\]]+','',s)

def score_pair(text):
    m=re.fullmatch(r'\s*(\d{1,2})\s*[-:]\s*(\d{1,2})\s*',text or '')
    if not m:return None
    h,a=map(int,m.groups());return (h,a) if h<=15 and a<=15 else None

def match_meta(row):
    # V4 matches_380.csv is a quality report; keep aliases so later schema changes remain compatible.
    def first(keys):
        for k in keys:
            if row.get(k):return row[k]
        return ''
    return first(('home_team','home','home_name','team_home')), first(('away_team','away','away_name','team_away')), first(('kickoff','match_time','time','date'))

def parse_visible_rows(html):
    soup=BeautifulSoup(html,'lxml');out=[]
    for tr in soup.find_all('tr'):
        cells=[x.get_text(' ',strip=True) for x in tr.find_all(['td','th'])]
        pairs=[(i,score_pair(c)) for i,c in enumerate(cells) if score_pair(c)]
        if not pairs:continue
        i,sc=pairs[0]
        # Standard league row: ... home | FT score | HT score | away ...
        if i<1 or i+1>=len(cells):continue
        home=cells[i-1];away=cells[i+1]
        # If next cell is half-time score, away is one further right.
        if score_pair(away) and i+2<len(cells):away=cells[i+2]
        if not home or not away:continue
        text=' | '.join(cells)
        out.append({'home':home,'away':away,'score':sc,'cells':cells,'text':text})
    return out

def choose(row,candidates):
    home,away,kick=match_meta(row);nh,na=norm(home),norm(away)
    if nh and na:
        exact=[x for x in candidates if norm(x['home'])==nh and norm(x['away'])==na]
        if len(exact)==1:return exact[0]
        fuzzy=[x for x in candidates if (nh in norm(x['home']) or norm(x['home']) in nh) and (na in norm(x['away']) or norm(x['away']) in na)]
        if len(fuzzy)==1:return fuzzy[0]
    # If team names are unavailable in V4, round is still a safe key only when a round has
    # exactly ten unresolved matches and ten visible result rows; pair by kickoff/order later.
    return None

async def main():
    ap=argparse.ArgumentParser();ap.add_argument('--matches',required=True);ap.add_argument('--out',default='data/history/v51_results/matches_380_results.csv');ap.add_argument('--wait',type=float,default=1.0);args=ap.parse_args()
    rows=read_csv(args.matches);out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    byid={r.get('match_id',''):r for r in rows};found={};round_rows={}
    print('input_columns',list(rows[0].keys()) if rows else [])
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True);page=await b.new_page(locale='zh-CN',viewport={'width':1600,'height':1200})
        await page.goto(LEAGUE_URL,wait_until='networkidle',timeout=90000);await page.wait_for_timeout(3000)
        for rnd in range(1,39):
            try:
                loc=page.get_by_text(str(rnd),exact=True)
                if await loc.count():await loc.first.click(timeout=5000);await page.wait_for_timeout(args.wait*1000)
            except Exception as e:print('round click warning',rnd,repr(e))
            vis=parse_visible_rows(await page.content());round_rows[rnd]=vis
            new=0
            for mid,r in byid.items():
                if mid in found:continue
                rr=str(r.get('round','')).strip()
                if rr and rr!=str(rnd):continue
                x=choose(r,vis)
                if x:found[mid]=(x['score'][0],x['score'][1],rnd,x);new+=1
            print('round',rnd,'visible_result_rows',len(vis),'matched_new',new,'total_results',len(found))
        await b.close()
    for mid,(h,a,rnd,x) in found.items():
        r=byid[mid];r['home_goals']=h;r['away_goals']=a;r['result']='H' if h>a else 'D' if h==a else 'A';r['result_source']='league_team_match';r['result_round']=rnd
    failed=sorted(set(byid)-set(found),key=lambda x:int(x))
    write_csv(out,rows)
    print('='*78);print('V5.1 赛果补齐 - team/date mapping');print('matches',len(rows),'results',len(found),'missing',len(failed));print('missing_ids',failed[:30], '...' if len(failed)>30 else '');print('output',out);print('='*78)
    if len(found)!=len(rows):
        raise SystemExit(f'RESULT ENRICH FAILED: expected {len(rows)} got {len(found)}; inspect input_columns and visible_result_rows before changing parser again')

if __name__=='__main__':asyncio.run(main())
