import argparse
import asyncio
import csv
import re
from datetime import datetime
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
    m=re.fullmatch(r'\s*(\d{1,2})\s*[-:]\s*(\d{1,2})\s*',text or '')
    if not m:return None
    h,a=map(int,m.groups())
    return (h,a) if h<=15 and a<=15 else None

def clean_team(s):
    s=re.sub(r'\[[^\]]*\]','',s or '')
    s=re.sub(r'\^\{[^}]*\}','',s)
    s=re.sub(r'\s+\d+$','',s.strip())
    return s.strip()

def parse_kickoff(s):
    for fmt in ('%Y-%m-%d %H:%M','%Y/%m/%d %H:%M','%Y-%m-%d %H:%M:%S','%Y/%m/%d %H:%M:%S'):
        try:return datetime.strptime((s or '').strip(),fmt)
        except ValueError:pass
    return datetime.max

def extract_visible_results(html,rnd):
    soup=BeautifulSoup(html,'lxml');out=[]
    for tr in soup.find_all('tr'):
        cells=[x.get_text(' ',strip=True) for x in tr.find_all(['td','th'])]
        if len(cells)<6 or cells[0].strip()!=str(rnd):continue
        score_idx=None;sc=None
        for i,c in enumerate(cells):
            z=score_pair(c)
            if z:
                score_idx=i;sc=z;break
        if score_idx is None or score_idx<3 or score_idx+2>=len(cells):continue
        # Official league row layout: round,time,home,FT,HT,away,...
        home=clean_team(cells[score_idx-1])
        away=clean_team(cells[score_idx+2])
        out.append({'round':rnd,'time':cells[1],'home':home,'away':away,'home_goals':sc[0],'away_goals':sc[1],'cells':cells})
    return out

async def main():
    ap=argparse.ArgumentParser();ap.add_argument('--matches',required=True);ap.add_argument('--out',default='data/history/v51_results/matches_380_results.csv');ap.add_argument('--wait',type=float,default=0.8);args=ap.parse_args()
    rows=read_csv(args.matches);out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    print('input_columns',list(rows[0].keys()) if rows else [])
    # V4.3 deliberately contains only quality metadata: match_id, round, kickoff, etc.
    # There are no team names.  But each EPL round has exactly 10 matches, so use the
    # canonical order already preserved by match_id/kickoff and independently scrape the
    # 10 official result rows for that round.  Pair by kickoff order; match_id is a final
    # tie-breaker.  This avoids inventing a team-name join that the verified file cannot do.
    by_round={i:[] for i in range(1,39)}
    for r in rows:
        try:by_round[int(r.get('round') or 0)].append(r)
        except ValueError:pass
    for rr in by_round.values():rr.sort(key=lambda r:(parse_kickoff(r.get('kickoff')),int(r.get('match_id') or 0)))
    found=0;problems=[]
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True);page=await b.new_page(locale='zh-CN',viewport={'width':1600,'height':1200})
        await page.goto(LEAGUE_URL,wait_until='domcontentloaded',timeout=90000);await page.wait_for_timeout(2500)
        for rnd in range(1,39):
            try:
                loc=page.get_by_text(str(rnd),exact=True)
                if await loc.count():await loc.first.click(timeout=5000);await page.wait_for_timeout(args.wait*1000)
            except Exception as e:print('round click warning',rnd,repr(e))
            visible=extract_visible_results(await page.content(),rnd)
            targets=by_round.get(rnd,[])
            # League rows are chronological; preserve stable DOM order for equal kickoff.
            # Verified targets are also chronological. EPL has 10 matches per round.
            print('round',rnd,'verified_matches',len(targets),'visible_result_rows',len(visible))
            if len(targets)!=10 or len(visible)!=10:
                problems.append(f'round {rnd}: verified={len(targets)} visible={len(visible)}');continue
            # Sort visible by parsed MM-DD HH:MM where possible, otherwise keep DOM order.
            # Season year is irrelevant within a single round; textual MM-DD HH:MM is enough.
            def vkey(x):
                m=re.search(r'(\d{1,2})-(\d{1,2})\s*(\d{1,2}):(\d{2})',x['time'].replace('\n',' '))
                return tuple(map(int,m.groups())) if m else (99,99,99,99)
            visible=sorted(enumerate(visible),key=lambda z:(vkey(z[1]),z[0]))
            for target,(_,game) in zip(targets,visible):
                h,a=game['home_goals'],game['away_goals']
                target['home_goals']=h;target['away_goals']=a;target['result']='H' if h>a else 'D' if h==a else 'A'
                target['result_source']='league_round_order';target['result_home_team']=game['home'];target['result_away_team']=game['away'];found+=1
            print('round',rnd,'mapped',10,'total_results',found)
        await b.close()
    write_csv(out,rows)
    print('='*78);print('V5.1 赛果补齐 - round/order mapping');print('matches',len(rows),'results',found,'missing',len(rows)-found);print('problems',problems);print('output',out);print('='*78)
    if found!=len(rows):raise SystemExit(f'RESULT ENRICH FAILED: expected {len(rows)} got {found}; problems={problems}')

if __name__=='__main__':asyncio.run(main())
