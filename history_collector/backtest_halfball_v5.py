import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    if not rows: return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with open(path,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)


def num(v):
    try:return float(v)
    except:return None


def norm_line(s):
    s=(s or "").strip().replace(" ","")
    aliases={"平/半":"平半","平半":"平半","半球":"半球","半/一":"半一","半一":"半一","一球":"一球","平手":"平手","受平/半":"受平半","受半球":"受半球","受半/一":"受半一"}
    return aliases.get(s,s)


def classify(open_line, close_line):
    o,c=norm_line(open_line),norm_line(close_line)
    if c!="半球": return None
    if o=="平手":return "平手→半球"
    if o=="平半":return "平半→半球"
    if o=="半球":return "半球→半球"
    if o=="半一":return "半一→半球"
    if o=="一球":return "一球→半球"
    return "其他→半球"


def wilson(k,n,z=1.96):
    if not n:return (0,0)
    p=k/n; den=1+z*z/n
    center=(p+z*z/(2*n))/den
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return center-half,center+half


def find_result_columns(row):
    # tolerate future result-enrichment schemas
    home_goals=next((row.get(k) for k in ("home_goals","home_score","score_home","hg") if row.get(k) not in (None,"")),None)
    away_goals=next((row.get(k) for k in ("away_goals","away_score","score_away","ag") if row.get(k) not in (None,"")),None)
    result=next((row.get(k) for k in ("result","result_1x2","ft_result","outcome") if row.get(k)),None)
    if result:
        x=result.strip().upper()
        if x in ("H","HOME","主胜"):return "H"
        if x in ("D","DRAW","平"):return "D"
        if x in ("A","AWAY","客胜"):return "A"
    try:
        h,a=int(float(home_goals)),int(float(away_goals))
        return "H" if h>a else "D" if h==a else "A"
    except:return None


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--snapshots",required=True)
    ap.add_argument("--matches",required=True)
    ap.add_argument("--out",default="data/history/v5_halfball")
    args=ap.parse_args(); out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    snaps=read_csv(args.snapshots); matches=read_csv(args.matches)
    match_map={r["match_id"]:r for r in matches if r.get("match_id")}

    # one record per match/company from AH OPEN+CLOSE, plus company 1X2 OPEN+CLOSE
    idx=defaultdict(dict)
    for r in snaps:
        if r.get("snapshot_type") not in ("OPEN","CLOSE"):continue
        idx[(r.get("match_id"),r.get("company_id"),r.get("market"))][r["snapshot_type"]]=r

    company_rows=[]
    for (mid,cid,mkt),pair in idx.items():
        if mkt!="AH" or "OPEN" not in pair or "CLOSE" not in pair:continue
        ah_o,ah_c=pair["OPEN"],pair["CLOSE"]
        cat=classify(ah_o.get("line"),ah_c.get("line"))
        if not cat:continue
        euro=idx.get((mid,cid,"1X2"),{})
        eo,ec=euro.get("OPEN"),euro.get("CLOSE")
        hmove=amove=None; hdrop_pct=None
        if eo and ec:
            oh,ch=num(eo.get("home")),num(ec.get("home")); oa,ca=num(eo.get("away")),num(ec.get("away"))
            if None not in (oh,ch):
                hmove=ch-oh; hdrop_pct=(oh-ch)/oh if oh else None
            if None not in (oa,ca):amove=ca-oa
        result=find_result_columns(match_map.get(mid,{}))
        veto1=(norm_line(ah_o.get("line"))=="平半" and norm_line(ah_c.get("line"))=="半球" and hmove is not None and hmove<=-0.15 and amove is not None and amove>=0.20)
        veto2=(norm_line(ah_c.get("line"))=="半球" and hdrop_pct is not None and hdrop_pct>=0.10 and amove is not None and amove>=0.20 and not veto1)
        original=(norm_line(ah_o.get("line"))=="半球" and norm_line(ah_c.get("line"))=="半球" and ec is not None and num(ec.get("home")) is not None and 1.90<=num(ec.get("home"))<=2.10 and hmove is not None and hmove<=0.03 and amove is not None and amove>=-0.02)
        company_rows.append({"match_id":mid,"company_id":cid,"category":cat,"ah_open":ah_o.get("line"),"ah_close":ah_c.get("line"),"home_open":eo.get("home") if eo else "","home_close":ec.get("home") if ec else "","away_open":eo.get("away") if eo else "","away_close":ec.get("away") if ec else "","home_move":hmove,"away_move":amove,"home_drop_pct":hdrop_pct,"result":result or "","original_candidate":original,"veto01":veto1,"veto02":veto2})

    # match-level consensus: majority category; model/veto require >=2 companies and >=50% of comparable companies
    bymatch=defaultdict(list)
    for r in company_rows:bymatch[r["match_id"]].append(r)
    match_rows=[]
    for mid,rs in bymatch.items():
        cats=Counter(r["category"] for r in rs);cat,ncat=cats.most_common(1)[0]
        comp=[r for r in rs if r["home_move"] is not None and r["away_move"] is not None]
        def consensus(key):
            n=sum(bool(r[key]) for r in comp); return n, bool(comp) and n>=2 and n/len(comp)>=0.5
        no,orig=consensus("original_candidate"); nv1,v1=consensus("veto01"); nv2,v2=consensus("veto02")
        result=find_result_columns(match_map.get(mid,{}))
        match_rows.append({"match_id":mid,"category":cat,"category_companies":ncat,"ah_companies":len(rs),"comparable_companies":len(comp),"result":result or "","original_votes":no,"original":orig,"veto01_votes":nv1,"veto01":v1,"veto02_votes":nv2,"veto02":v2})

    def summary(label, selected):
        rr=[r for r in match_rows if selected(r) and r["result"] in ("H","D","A")]; n=len(rr); c=Counter(r["result"] for r in rr); an=c["D"]+c["A"];lo,hi=wilson(an,n)
        return {"group":label,"n":n,"home_win":c["H"],"draw":c["D"],"away_win":c["A"],"home_win_pct":round(c["H"]/n*100,2) if n else "","draw_pct":round(c["D"]/n*100,2) if n else "","away_win_pct":round(c["A"]/n*100,2) if n else "","away_not_lose_pct":round(an/n*100,2) if n else "","away_not_lose_ci95":f"{lo*100:.1f}-{hi*100:.1f}" if n else ""}

    summaries=[]
    for cat in ("平手→半球","平半→半球","半球→半球","半一→半球","一球→半球","其他→半球"):
        summaries.append(summary(cat,lambda r,c=cat:r["category"]==c))
    summaries += [summary("原半球假强候选",lambda r:r["original"]),summary("VETO-01",lambda r:r["veto01"]),summary("VETO-02",lambda r:r["veto02"]),summary("原模型排除VETO后",lambda r:r["original"] and not r["veto01"] and not r["veto02"])]

    write_csv(out/"halfball_company_samples.csv",company_rows);write_csv(out/"halfball_match_samples.csv",match_rows);write_csv(out/"backtest_summary.csv",summaries)
    result_count=sum(r["result"] in ("H","D","A") for r in match_rows)
    report={"snapshot_rows":len(snaps),"matches_input":len(matches),"halfball_company_samples":len(company_rows),"halfball_matches":len(match_rows),"halfball_matches_with_result":result_count,"summary":summaries}
    (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("="*78);print("V5 半球盘口分类与真实赛果回测");print("snapshots",len(snaps),"matches",len(matches));print("halfball_matches",len(match_rows),"with_result",result_count)
    if result_count==0:print("RESULT_GATE FAIL: 当前380场数据集中没有可识别的最终赛果字段，必须先补齐赛果，禁止输出伪命中率。")
    else:
        for r in summaries:print(r)
    print("输出",out);print("="*78)
    if result_count==0:raise SystemExit(2)

if __name__=="__main__":main()
