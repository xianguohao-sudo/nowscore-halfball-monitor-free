import asyncio,csv,json,re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from history_collector.validate_history_v33 import parse_detail_html,parse_kickoff,choose_at_or_before,fnum

STATE=Path('data/state.json')
OUT=Path('data/recheck_original_halfball_14')
TARGETS={
'奥卡斯 vs 马卡拉':1,'清水鼓动 vs 千叶市原':0,'鸟栖沙岩 vs 磐城FC':1,'冈山绿雉 vs 京都不死鸟':1,
'横滨水手 vs 水户蜀葵':0,'博洛尼亚 vs 都灵':1,'吉维森特 vs 马里迪莫':1,'圣约翰斯通 vs 福尔柯克':1,
'帕尔杜比斯 vs 波希米亚1905':0,'牛津联队 vs 剑桥联':1,'科鲁姆 vs 阿拉尼亚体育':1,'艾华卡 vs 里奥阿维':0,
'米拉索 vs 博塔弗戈':0,'阿古雷利 vs 托尔':0}

def half(s): return (s or '').replace(' ','') in {'半球','0.5','-0.5','半'}
def threeq(s): return (s or '').replace(' ','') in {'半/一','半一','0.75','-0.75'}
def med(xs): return median(xs) if xs else None

def evaluate(snaps):
    # snaps: company -> {'AH_OPEN','AH_AT','1X2_OPEN','1X2_AT'} reconstructed at first alert time
    good=[v for v in snaps.values() if v.get('AH_AT') and v.get('1X2_AT') and v.get('1X2_OPEN')]
    ah=[v for v in good if v['AH_AT'].get('line')]
    hs=[v for v in ah if half(v['AH_AT']['line'])]
    mh=med([fnum(v['1X2_AT']['home']) for v in good if fnum(v['1X2_AT']['home']) is not None])
    maway=med([fnum(v['AH_AT']['away'])+1 for v in hs if fnum(v['AH_AT']['away']) is not None])
    hm=med([fnum(v['1X2_AT']['home'])-fnum(v['1X2_OPEN']['home']) for v in good if fnum(v['1X2_AT']['home']) and fnum(v['1X2_OPEN']['home'])])
    am=med([fnum(v['1X2_AT']['away'])-fnum(v['1X2_OPEN']['away']) for v in good if fnum(v['1X2_AT']['away']) and fnum(v['1X2_OPEN']['away'])])
    now75=sum(1 for v in ah if threeq(v['AH_AT']['line']))
    open75=sum(1 for v in ah if v.get('AH_OPEN') and threeq(v['AH_OPEN'].get('line')))
    c1=len(hs)>=max(3,(len(ah)+1)//2) if ah else False
    c2=mh is not None and 1.90<=mh<=2.10
    c3=maway is not None and 1.70<=maway<=1.85
    c4=hm is not None and hm<=0.03
    c5=am is not None and am>=-0.02
    c6=now75==0 and open75<=max(1,len(ah)//4) if ah else False
    # Original strict fingerprint: all market-structure conditions required. No 4/6 relaxation.
    strict=all((c1,c2,c3,c4,c5,c6))
    return dict(strict_keep=strict,c1=c1,c2=c2,c3=c3,c4=c4,c5=c5,c6=c6,score=sum((c1,c2,c3,c4,c5,c6)),companies=len(good),ah_companies=len(ah),half_companies=len(hs),home_now=mh,away_half=maway,home_move=hm,away_move=am)

async def main():
    state=json.loads(STATE.read_text(encoding='utf-8'))
    selected=[]
    for key,v in state.items():
        if key.startswith(('qr:','pkrh:')): continue
        if v.get('match') in TARGETS:
            selected.append((key,v))
    print('selected',len(selected),[x[1]['match'] for x in selected])
    rows=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);ctx=await browser.new_context(locale='zh-CN',viewport={'width':1600,'height':1200});page=await ctx.new_page()
        for i,(mid,s) in enumerate(selected,1):
            url=f'https://live.nowscore.com/odds/match/{mid}.htm'; print(f'[{i}/{len(selected)}]',mid,s['match'])
            try:
                await page.goto(url,wait_until='domcontentloaded',timeout=90000);await page.wait_for_timeout(500)
                kr=await page.locator('#hide_matchTime').get_attribute('value') if await page.locator('#hide_matchTime').count() else None
                kickoff=parse_kickoff(kr); target=datetime.strptime(s['first_sent_at'],'%Y-%m-%d %H:%M:%S')
                hrefs=await page.locator('a[href*="3in1Odds.aspx"]').evaluate_all("els=>els.map(e=>e.getAttribute('href'))")
                details=[]
                for h in hrefs:
                    if not h: continue
                    full=urljoin(url,h);m=re.search(r'companyid=(\d+).*?[?&]id=(\d+)',full,re.I)
                    if m and m.group(2)==mid and (m.group(1),full) not in details: details.append((m.group(1),full))
                snaps={}
                for cid,du in details[:17]:
                    dp=await ctx.new_page()
                    try:
                        rr=await dp.goto(du,wait_until='domcontentloaded',timeout=90000);await dp.wait_for_timeout(250)
                        if not rr or rr.status!=200: continue
                        parsed=parse_detail_html(await dp.content(),mid,cid,kickoff,du)
                        z={}
                        for market in ('AH','1X2'):
                            rs=[r for r in parsed if r['usable'] and r['market']==market]
                            rs.sort(key=lambda r:r['change_time'])
                            if rs:
                                z[market+'_OPEN']=rs[0]; at=choose_at_or_before(rs,target)
                                if at:z[market+'_AT']=at
                        if z:snaps[cid]=z
                    finally: await dp.close()
                ev=evaluate(snaps); hit=TARGETS[s['match']]
                rows.append(dict(match_id=mid,match=s['match'],first_sent_at=s['first_sent_at'],kickoff=s['kickoff'],actual_away_not_lose=hit,**ev))
                print(' ', 'KEEP' if ev['strict_keep'] else 'FILTER', 'score',ev['score'],'/6','actual','HIT' if hit else 'MISS','home_now',ev['home_now'],'away+0.5',ev['away_half'],'moves',ev['home_move'],ev['away_move'])
            except Exception as e:
                print(' ERROR',repr(e));rows.append(dict(match_id=mid,match=s['match'],error=repr(e),actual_away_not_lose=TARGETS[s['match']]))
        await browser.close()
    OUT.mkdir(parents=True,exist_ok=True)
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys:keys.append(k)
    with (OUT/'report.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
    kept=[r for r in rows if r.get('strict_keep')];kh=sum(r['actual_away_not_lose'] for r in kept); correct_total=sum(r['actual_away_not_lose'] for r in rows);miss_total=len(rows)-correct_total
    filtered=[r for r in rows if not r.get('strict_keep') and 'error' not in r];filtered_miss=sum(1 for r in filtered if not r['actual_away_not_lose']);filtered_hit=sum(1 for r in filtered if r['actual_away_not_lose'])
    summary={'total':len(rows),'original_hits':correct_total,'original_misses':miss_total,'strict_kept':len(kept),'strict_kept_hits':kh,'strict_kept_misses':len(kept)-kh,'strict_hit_rate':round(kh/len(kept)*100,2) if kept else None,'filtered_original_misses':filtered_miss,'filtered_original_hits':filtered_hit,'errors':sum('error' in r for r in rows)}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('='*78);print('原14场:',correct_total,'中',miss_total,'失');print('严格原始规则保留:',len(kept),'场',kh,'中',len(kept)-kh,'失','命中率',summary['strict_hit_rate']);print('过滤错误:',filtered_miss,'/ ',miss_total,'; 同时误杀正确:',filtered_hit,'/ ',correct_total);print('errors',summary['errors']);print('='*78)

if __name__=='__main__': asyncio.run(main())
