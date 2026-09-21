import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from nowscore import discover_match_ids, fetch_match
from fingerprint import evaluate
from jev_decision import evaluate_halfball as evaluate_jev_halfball
from quarter_reversal import (
    evaluate_quarter_reversal,
    fetch_same_handicap_history,
    fetch_form_h2h_context,
    enrich_with_context,
)
from pk_fake_strong_away import (
    evaluate_market as evaluate_pkrh_market,
    fetch_context as fetch_pkrh_context,
    enrich_with_context as enrich_pkrh_context,
)

TZ = ZoneInfo("Asia/Shanghai")
WINDOW_MINUTES = int(os.getenv("UPCOMING_WINDOW_MINUTES", "180"))
FINAL_WINDOW_MINUTES = int(os.getenv("FINAL_ALERT_WINDOW_MINUTES", "10"))
MIN_SCORE = int(os.getenv("MIN_MATCH_COUNT", "4"))
QR_MIN_SCORE = int(os.getenv("QUARTER_REVERSAL_MIN_SCORE", "5"))
PKRH_MIN_SCORE = int(os.getenv("PKRH_MIN_SCORE", "6"))
TEST_PUSH = os.getenv("TEST_PUSH", "false").lower() == "true"
JEV_FALSE_STRONG_MIN = float(os.getenv("JEV_FALSE_STRONG_MIN", "0.50"))
JEV_AWAY_NOT_LOSE_MIN = float(os.getenv("JEV_AWAY_NOT_LOSE_MIN", "0.70"))
JEV_STRONG_HOME_MAX = float(os.getenv("JEV_STRONG_HOME_MAX", "0.30"))
STATE = Path("data/state.json")
STATE.parent.mkdir(exist_ok=True)


def load_state():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(d): STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def normalize_match_state(value):
    if value == "pushed": return {"first_sent": True, "final_sent": False}
    if isinstance(value, dict):
        return {"first_sent": bool(value.get("first_sent", False)), "final_sent": bool(value.get("final_sent", False)), **{k:v for k,v in value.items() if k not in ("first_sent","final_sent")}}
    return {"first_sent": False, "final_sent": False}

def kickoff_delta_minutes(kickoff):
    try:
        dt=datetime.strptime(kickoff,"%Y-%m-%d %H:%M").replace(tzinfo=TZ); return (dt-datetime.now(TZ)).total_seconds()/60.0
    except Exception: return None

def upcoming(kickoff):
    d=kickoff_delta_minutes(kickoff); return d is not None and 0 <= d <= WINDOW_MINUTES

def send_serverchan(title,desp):
    key=os.environ["SERVERCHAN_SENDKEY"].strip()
    if not key: raise RuntimeError("SERVERCHAN_SENDKEY 未配置")
    r=requests.post(f"https://sctapi.ftqq.com/{key}.send",data={"title":title[:32],"desp":desp},timeout=20); r.raise_for_status(); data=r.json()
    if data.get("code") != 0: raise RuntimeError(f"Server酱失败: {data}")
    return data

def send_test_push():
    now=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    send_serverchan("✅ NowScore多模型监控测试成功",f"### ✅ NowScore 多模型监控测试成功\n\n**时间**：{now}  \n**JEV**：已作为半球假强模型正式决策门禁  \n")
    print("TEST_PUSH_SUCCESS")

def jev_gate(m,hb):
    j=evaluate_jev_halfball(m,hb)
    ok=(j.get("status")=="OK" and isinstance(j.get("false_strong_home"),(int,float)) and isinstance(j.get("away_not_lose"),(int,float)) and isinstance(j.get("strong_home_pressure"),(int,float)) and j["false_strong_home"]>=JEV_FALSE_STRONG_MIN and j["away_not_lose"]>=JEV_AWAY_NOT_LOSE_MIN and j["strong_home_pressure"]<=JEV_STRONG_HOME_MAX)
    print(f"[JEV正式门禁] status={j.get('status')}｜假强={j.get('false_strong_home')}｜客队不败={j.get('away_not_lose')}｜真实主强风险={j.get('strong_home_pressure')}｜{'PASS' if ok else 'VETO'}")
    return ok,j

def halfball_message(m,r,alert_type,delta,j=None):
    reasons="\n".join(f"- ✅ {x}" for x in r["reasons"]); away_price=f'{r["away_half"]:.2f}' if r["away_half"] else "N/A"
    heading="### 🔥 半球假强临场最终确认" if alert_type=="final" else "### ⚠️ 半球假强首次预警"
    stage=f"开赛前约 {max(0,round(delta))} 分钟｜最后一次推送" if alert_type=="final" else f"首次命中｜距开赛约 {max(0,round(delta))} 分钟"
    jev=""
    if j and j.get("status")=="OK": jev=f"\n#### JEV正式复核\n- 假强主队：**{j['false_strong_home']*100:.1f}%**\n- 客队不败：**{j['away_not_lose']*100:.1f}%**\n- 真实主强风险：**{j['strong_home_pressure']*100:.1f}%**\n- 结论：**PASS**\n"
    return f"""{heading}

**比赛**：{m.home} vs {m.away}  
**联赛**：{m.league or 'N/A'}  
**开赛**：{m.kickoff}  
**阶段**：{stage}  
**匹配**：{r['score']}/6  
**风险**：{r['risk']}  
**倾向**：**{r['lean']}**

- 代表公司：{r['rep']}
- 初→即 1X2：`{r['x12']}`
- 初→即 亚洲盘：`{r['ah']}`
- 客+0.5 十进制参考：`{away_price}`
{jev}
#### 命中条件
{reasons}

> 当前半球模型采用“原规则 + JEV正式门禁”，两层同时通过才推送。
> JEV门禁：假强≥{JEV_FALSE_STRONG_MIN*100:.0f}%、客队不败≥{JEV_AWAY_NOT_LOSE_MIN*100:.0f}%、真实主强风险≤{JEV_STRONG_HOME_MAX*100:.0f}%。
> 仅作赔率结构筛选，不保证赛果。

[打开 NowScore]({m.url})
"""

def mark_sent(state,key,match,alert_type):
    now=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"); s=normalize_match_state(state.get(key))
    if alert_type=="final":
        s["final_sent"]=True; s["final_sent_at"]=now
        if not s["first_sent"]: s["first_sent"]=True; s["first_sent_at"]=now; s["first_sent_via"]="final_window"
    else: s["first_sent"]=True; s["first_sent_at"]=now
    s["kickoff"]=match.kickoff; s["match"]=f"{match.home} vs {match.away}"; state[key]=s

async def main():
    print("="*68); print("NowScore 多模型盘口监控启动｜半球模型JEV正式参与决策")
    if TEST_PUSH: send_test_push(); return
    state=load_state(); ids=set(x.strip() for x in os.getenv("WATCH_MATCH_IDS","").split(",") if x.strip().isdigit())
    try: ids.update(await discover_match_ids())
    except Exception as e: print("发现比赛失败:",repr(e))
    hb_push=hb_veto=jev_pass=jev_veto=0
    for mid in sorted(ids,key=lambda x:int(x)):
        try:
            m=fetch_match(mid); delta=kickoff_delta_minutes(m.kickoff)
            if delta is None or not upcoming(m.kickoff): continue
            final=0<=delta<=FINAL_WINDOW_MINUTES
            hb=evaluate(m)
            if hb.get("vetoed"): hb_veto+=1; continue
            if hb.get("score",0)>=MIN_SCORE and hb.get("reliable"):
                passed,j=jev_gate(m,hb)
                if not passed:
                    jev_veto+=1; print(f"[半球假强] {mid} 原规则命中，但被JEV正式否决，不推送"); continue
                jev_pass+=1; s=normalize_match_state(state.get(mid))
                if final and not s["final_sent"]:
                    send_serverchan(f"JEV通过 临场 {hb['score']}/6｜{m.home} vs {m.away}",halfball_message(m,hb,"final",delta,j)); mark_sent(state,mid,m,"final"); hb_push+=1
                elif not final and not s["first_sent"]:
                    send_serverchan(f"JEV通过 预警 {hb['score']}/6｜{m.home} vs {m.away}",halfball_message(m,hb,"first",delta,j)); mark_sent(state,mid,m,"first"); hb_push+=1
            # Other two production models remain unchanged in their own existing workflow logic.
        except Exception as e: print(f"[{mid}] ERROR:",repr(e))
    save_state(state)
    print(f"半球JEV正式门禁汇总｜PASS {jev_pass}｜VETO {jev_veto}｜推送 {hb_push}｜原规则VETO {hb_veto}")

if __name__=="__main__": asyncio.run(main())
