import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

from nowscore import discover_match_ids, fetch_match
from fingerprint import evaluate

TZ = ZoneInfo("Asia/Shanghai")
WINDOW_MINUTES = int(os.getenv("UPCOMING_WINDOW_MINUTES", "180"))
MIN_SCORE = int(os.getenv("MIN_MATCH_COUNT", "4"))
STATE = Path("data/state.json")
STATE.parent.mkdir(exist_ok=True)

def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(d):
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def upcoming(kickoff):
    try:
        dt = datetime.strptime(kickoff, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        delta = dt - datetime.now(TZ)
        return timedelta(minutes=-3) <= delta <= timedelta(minutes=WINDOW_MINUTES)
    except Exception:
        return False

def send_serverchan(title, desp):
    key = os.environ["SERVERCHAN_SENDKEY"].strip()
    r = requests.post(f"https://sctapi.ftqq.com/{key}.send",
                      data={"title": title[:32], "desp": desp}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Server酱失败: {data}")

def message(m, r):
    reasons = "\n".join(f"- ✅ {x}" for x in r["reasons"])
    away_price = f'{r["away_half"]:.2f}' if r["away_half"] else "N/A"
    return f"""### ⚠️ 半球假强主队预警

**比赛**：{m.home} vs {m.away}  
**联赛**：{m.league or 'N/A'}  
**开赛**：{m.kickoff}  
**匹配**：{r["score"]}/6  
**风险**：{r["risk"]}  
**倾向**：**{r["lean"]}**

- 代表公司：{r["rep"]}
- 初→即 1X2：`{r["x12"]}`
- 初→即 亚洲盘：`{r["ah"]}`
- 客+0.5 十进制参考：`{away_price}`

#### 命中条件
{reasons}

> 客队近期客场/赢盘能力需独立数据源确认，本版不臆测该项。  
> 仅作赔率结构筛选，不保证赛果。

[打开 NowScore]({m.url})
"""

async def main():
    state = load_state()
    ids = set(x.strip() for x in os.getenv("WATCH_MATCH_IDS","").split(",") if x.strip().isdigit())
    try:
        ids.update(await discover_match_ids())
    except Exception as e:
        print("发现比赛失败:", repr(e))

    print("候选比赛数:", len(ids))
    changed = False

    for mid in sorted(ids):
        try:
            if state.get(mid) == "pushed":
                continue
            m = fetch_match(mid)
            if not upcoming(m.kickoff):
                continue
            r = evaluate(m)
            print(mid, m.home, "vs", m.away, f'{r["score"]}/6', "reliable=", r["reliable"])
            if r["score"] >= MIN_SCORE and r["reliable"]:
                send_serverchan(f"半球假强 {r['score']}/6｜{m.home} vs {m.away}", message(m,r))
                state[mid] = "pushed"
                changed = True
                print("PUSHED", mid)
        except Exception as e:
            print("ERROR", mid, repr(e))

    if changed:
        save_state(state)

if __name__ == "__main__":
    asyncio.run(main())
