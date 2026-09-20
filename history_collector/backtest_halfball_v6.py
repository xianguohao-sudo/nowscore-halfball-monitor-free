import argparse,csv,json,math
from collections import defaultdict,Counter
from pathlib import Path

def read(p):
    with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def num(v):
    try:return float(v)
    except:return None
def norm(s):return {'平/半':'平半','半/一':'半一'}.get((s or '').strip(),(s or '').strip())
def result(r):
    x=(r.get('result') or '').upper()
    if x in ('H','D','A'):return x
    try:
        h=int(float(r.get('home_goals')));a=int(float(r.get('away_goals')));return 'H' if h>a else 'D' if h==a else 'A'
    except:return ''
def wilson(k,n,z=1.96):
    if not n:return (0,0)
    p=k/n;d=1+z*z/n;c=(p+z*z/(2*n))/d;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;return c-h,c+h
def write(p,rows):
    if not rows:return
    with open(p,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--snapshots',required=True);ap.add_argument('--matches',required=True);ap.add_argument('--out',default='data/history/v6_halfball');a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    ss=read(a.snapshots);mm=read(a.matches);mmap={r['match_id']:r for r in mm}
    idx=defaultdict(dict)
    for r in ss:
        if r.get('snapshot_type') in ('OPEN','CLOSE'):idx[(r.get('match_id'),r.get('company_id'),r.get('market'))][r['snapshot_type']]=r
    cr=[]
    for (mid,cid,mkt),p in idx.items():
        if mkt!='AH' or 'OPEN' not in p or 'CLOSE' not in p:continue
        ao,ac=p['OPEN'],p['CLOSE'];eoec=idx.get((mid,cid,'1X2'),{});eo,ec=eoec.get('OPEN'),eoec.get('CLOSE')
        if norm(ac.get('line'))!='半球' or not eo or not ec:continue
        oh,ch,oa,ca=map(num,[eo.get('home'),ec.get('home'),eo.get('away'),ec.get('away')])
        if None in (oh,ch,oa,ca):continue
        hm=ch-oh;am=ca-oa;drop=(oh-ch)/oh if oh else 0
        # Six historical conditions. c3 uses AH away decimal odds converted from HK-style water by +1.
        away_water=num(ac.get('away'));away_dec=(away_water+1) if away_water is not None and away_water<1.5 else away_water
        c1=norm(ac.get('line'))=='半球';c2=1.90<=ch<=2.10;c3=away_dec is not None and 1.70<=away_dec<=1.85;c4=hm<=0.03;c5=am>=-0.02;c6=True # CLOSE half-ball means no closing -0.75; path detail retained separately
        score=sum((c1,c2,c3,c4,c5,c6));v1=(norm(ao.get('line'))=='平半' and hm<=-0.15 and am>=0.20);v2=(drop>=0.10 and am>=0.20 and not v1)
        cr.append({'match_id':mid,'company_id':cid,'open_line':ao.get('line'),'close_line':ac.get('line'),'home_open':oh,'home_close':ch,'away_open':oa,'away_close':ca,'away_half':away_dec,'home_move':hm,'away_move':am,'home_drop_pct':drop,'score':score,'strict6':score==6,'veto01':v1,'veto02':v2})
    by=defaultdict(list)
    for r in cr:by[r['match_id']].append(r)
    rows=[]
    for mid,rs in by.items():
        n=len(rs)
        def con(k):
            v=sum(bool(x[k]) for x in rs);return v,v>=2 and v/n>=.5
        strict_votes,strict=con('strict6');v1votes,v1=con('veto01');v2votes,v2=con('veto02')
        loose_votes=sum(x['score']>=4 for x in rs);loose=loose_votes>=2 and loose_votes/n>=.5
        rows.append({'match_id':mid,'result':result(mmap.get(mid,{})),'companies':n,'loose4_votes':loose_votes,'loose4':loose,'strict6_votes':strict_votes,'strict6':strict,'veto01_votes':v1votes,'veto01':v1,'veto02_votes':v2votes,'veto02':v2})
    groups=[('A_宽松4+/6',lambda r:r['loose4']),('B_严格6/6',lambda r:r['strict6']),('C_6/6+VETO01',lambda r:r['strict6'] and not r['veto01']),('D_6/6+VETO01+VETO02',lambda r:r['strict6'] and not r['veto01'] and not r['veto02'])]
    sums=[]
    for name,fn in groups:
        z=[r for r in rows if fn(r) and r['result'] in 'HDA'];n=len(z);c=Counter(r['result'] for r in z);hit=c['D']+c['A'];lo,hi=wilson(hit,n)
        sums.append({'group':name,'n':n,'hit_away_not_lose':hit,'miss_home_win':c['H'],'hit_rate_pct':round(hit/n*100,2) if n else '','home_win_pct':round(c['H']/n*100,2) if n else '','draw_pct':round(c['D']/n*100,2) if n else '','away_win_pct':round(c['A']/n*100,2) if n else '','ci95':f'{lo*100:.1f}-{hi*100:.1f}' if n else ''})
    write(out/'v6_match_samples.csv',rows);write(out/'v6_summary.csv',sums);(out/'report.json').write_text(json.dumps({'matches':len(mm),'halfball_matches':len(rows),'with_result':sum(bool(r['result']) for r in rows),'summary':sums},ensure_ascii=False,indent=2),encoding='utf-8')
    print('='*78);print('V6 原始半球假强大样本回测');print('matches',len(mm),'halfball',len(rows),'with_result',sum(bool(r['result']) for r in rows));
    for x in sums:print(x)
    print('='*78)
if __name__=='__main__':main()
