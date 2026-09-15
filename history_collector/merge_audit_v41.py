import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

SRC=Path("data/history/v4_batches")
OUT=Path("data/history/v41_final")

def read(pattern):
    rows=[]
    for p in sorted(SRC.rglob(pattern)):
        with p.open(encoding="utf-8-sig",newline="") as f: rows.extend(csv.DictReader(f))
    return rows

def write(path,rows):
    if not rows:return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys:keys.append(k)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def dt(s): return datetime.strptime(s,"%Y-%m-%d %H:%M")
def kickoff(s):
    for fmt in ("%Y/%m/%d %H:%M","%Y-%m-%d %H:%M"):
        try:return datetime.strptime(s,fmt)
        except:pass
    return None

def dedup(rows,keys):
    d={}
    for r in rows:d[tuple(r.get(k,"") for k in keys)]=r
    return list(d.values())

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    matches=dedup(read("matches_*.csv"),["match_id"])
    timeline=dedup(read("timeline_*.csv"),["match_id","company_id","market","change_time","minute","score","status","home","line","draw","away","over","under"])
    matches.sort(key=lambda r:(int(r.get("round") or 0),int(r["match_id"])))
    timeline.sort(key=lambda r:(int(r["match_id"]),int(r["company_id"]),r["market"],r["change_time"]))
    m={r["match_id"]:r for r in matches}; groups=defaultdict(list)
    for r in timeline:
        if str(r.get("usable","")).lower()=="true":groups[(r["match_id"],r["company_id"],r["market"])].append(r)
    snaps=[]
    for (mid,cid,mk),rs in groups.items():
        ko=kickoff(m[mid].get("kickoff","")) if mid in m else None
        if not ko:continue
        rs=sorted(rs,key=lambda r:r["change_time"])
        targets=[("OPEN",None),("T180",ko-timedelta(minutes=180)),("T60",ko-timedelta(minutes=60)),("T30",ko-timedelta(minutes=30)),("CLOSE",ko-timedelta(microseconds=1))]
        for label,target in targets:
            cand=rs[:1] if target is None else [r for r in rs if dt(r["change_time"])<=target]
            if cand:
                z=dict(cand[-1]);z["snapshot_type"]=label;z["kickoff"]=ko.strftime("%Y-%m-%d %H:%M")
                if target is not None:z["age_from_target_minutes"]=round((target-dt(z["change_time"])).total_seconds()/60,2)
                snaps.append(z)
    close=[r for r in snaps if r["snapshot_type"]=="CLOSE"]
    bad_close=[r for r in close if dt(r["change_time"])>=dt(r["kickoff"]) or "滚" in r.get("status","")]
    pass_matches=[r for r in matches if r.get("status")=="PASS"]
    contam=sum(int(r.get("contamination") or 0) for r in matches)
    ids={r["match_id"] for r in matches}
    timeline_ids={r["match_id"] for r in timeline}
    missing=sorted(ids-timeline_ids)
    audit={"matches":len(matches),"pass":len(pass_matches),"fail":len(matches)-len(pass_matches),"contamination":contam,"timeline_rows":len(timeline),"snapshot_rows":len(snaps),"close_rows":len(close),"bad_close":len(bad_close),"matches_without_timeline":missing,"gate_pass":len(matches)==380 and len(pass_matches)==380 and contam==0 and not bad_close and not missing}
    write(OUT/"matches_380.csv",matches);write(OUT/"odds_timeline_380.csv",timeline);write(OUT/"odds_snapshots_380.csv",snaps)
    (OUT/"audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print("="*78);print("V4.1 380场合并审计");
    for k,v in audit.items():print(k,v)
    print("="*78)
    if not audit["gate_pass"]:raise SystemExit("V4.1 AUDIT FAILED")
if __name__=="__main__":main()
