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
TEST_PUSH = os.getenv("TEST_PUSH", "false").lower() == "true"
STATE = Path("data/state.json")
STATE.parent.mkdir(exist_ok=True)

def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(d):
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def kickoff_delta_minutes(kickoff):
    try:
        dt = datetime.strptime(kickoff, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        return (dt - datetime.now(TZ)).total_seconds() / 60.0
    except Exception:
        return None

def upcoming(kickoff):
    delta = kickoff_delta_minutes(kickoff)
    return delta is not None and -3 <= delta <= WINDOW_MINUTES

def send_serverchan(title, desp):
    key = os.environ["SERVERCHAN_SENDKEY"].strip()
    if not key:
        raise RuntimeError("SERVERCHAN_SENDKEY 未配置")
    r = requests.post(f"https://sctapi.ftqq.com/{key}.send", data={"title": title[:32], "desp": desp}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Server酱失败: {data}")
    return data

def send_test_push():
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    desp = f"""### ✅ 半球假强监控测试成功\n\n**时间**：{now}  \n**来源**：GitHub Actions  \n**用途**：验证 GitHub Secret → Server酱 → 微信 推送链路\n\n如果你在微信收到这条消息，说明推送链路正常。\n"""
    send_serverchan("✅ 半球假强监控测试成功", desp)
    print("TEST_PUSH_SUCCESS: Server酱测试消息已发送")

def message(m, r):
    reasons = "\n".join(f"- ✅ {x}" for x in r["reasons"])
    away_price = f'{r["away_half"]:.2f}' if r["away_half"] else "N/A"
    return f"""### ⚠️ 半球假强主队预警\n\n**比赛**：{m.home} vs {m.away}  \n**联赛**：{m.league or 'N/A'}  \n**开赛**：{m.kickoff}  \n**匹配**：{r["score"]}/6  \n**风险**：{r["risk"]}  \n**倾向**：**{r["lean"]}**\n\n- 代表公司：{r["rep"]}\n- 初→即 1X2：`{r["x12"]}`\n- 初→即 亚洲盘：`{r["ah"]}`\n- 客+0.5 十进制参考：`{away_price}`\n\n#### 命中条件\n{reasons}\n\n> 仅作赔率结构筛选，不保证赛果。\n\n[打开 NowScore]({m.url})\n"""

async def main():
    print("=" * 60)
    print("半球假强主队监控启动")
    print("当前时间:", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
    print("检查未来分钟数:", WINDOW_MINUTES)
    print("最低匹配阈值:", MIN_SCORE)
    print("测试推送模式:", TEST_PUSH)
    print("=" * 60)

    if TEST_PUSH:
        send_test_push()
        return

    state = load_state()
    ids = set(x.strip() for x in os.getenv("WATCH_MATCH_IDS", "").split(",") if x.strip().isdigit())
    try:
        discovered = await discover_match_ids()
        ids.update(discovered)
        print(f"自动发现候选: {len(discovered)} 场")
    except Exception as e:
        print("发现比赛失败:", repr(e))

    print("候选比赛总数:", len(ids))
    parsed_count = upcoming_count = scored_count = pushed_count = 0
    changed = False

    for mid in sorted(ids):
        try:
            if state.get(mid) == "pushed":
                print(f"[{mid}] 跳过：此前已经推送")
                continue
            m = fetch_match(mid)
            parsed_count += 1
            delta = kickoff_delta_minutes(m.kickoff)
            print("-" * 60)
            print(f"[{mid}] {m.home} vs {m.away}")
            print(f"联赛: {m.league or 'N/A'}")
            print(f"开赛: {m.kickoff or 'N/A'}")
            print(f"赔率公司数: {len(m.rows)}")
            if delta is None:
                print("结果: 排除｜无法解析开赛时间")
                continue
            print(f"距离开赛: {delta:.1f} 分钟")
            if not upcoming(m.kickoff):
                print(f"结果: 排除｜不在未来 {WINDOW_MINUTES} 分钟监控窗口内")
                continue
            upcoming_count += 1
            r = evaluate(m)
            scored_count += 1
            print(f"评分: {r['score']}/6")
            print(f"数据可靠: {r['reliable']}")
            print(f"风险: {r['risk']}")
            print(f"倾向: {r['lean']}")
            print(f"初→即 1X2: {r['x12']}")
            print(f"初→即 亚洲盘: {r['ah']}")
            if r["score"] >= MIN_SCORE and r["reliable"]:
                send_serverchan(f"半球假强 {r['score']}/6｜{m.home} vs {m.away}", message(m, r))
                state[mid] = "pushed"
                changed = True
                pushed_count += 1
                print("结果: 符合 → 已推送微信")
            else:
                print("结果: 排除")
        except Exception as e:
            print(f"[{mid}] ERROR:", repr(e))

    if changed:
        save_state(state)

    print("=" * 60)
    print("本轮扫描汇总")
    print("候选比赛:", len(ids))
    print("成功解析赔率:", parsed_count)
    print("进入时间窗口:", upcoming_count)
    print("完成模型评分:", scored_count)
    print("本轮微信推送:", pushed_count)
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
