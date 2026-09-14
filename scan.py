import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

from nowscore import discover_match_ids, fetch_match
from fingerprint import evaluate

TZ = ZoneInfo("Asia/Shanghai")
WINDOW_MINUTES = int(os.getenv("UPCOMING_WINDOW_MINUTES", "180"))
FINAL_WINDOW_MINUTES = int(os.getenv("FINAL_ALERT_WINDOW_MINUTES", "10"))
MIN_SCORE = int(os.getenv("MIN_MATCH_COUNT", "4"))
TEST_PUSH = os.getenv("TEST_PUSH", "false").lower() == "true"
STATE = Path("data/state.json")
STATE.parent.mkdir(exist_ok=True)


def load_state():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(d):
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_match_state(value):
    """兼容旧版 state.json 中 mid: 'pushed' 的格式。"""
    if value == "pushed":
        return {"first_sent": True, "final_sent": False}
    if isinstance(value, dict):
        return {
            "first_sent": bool(value.get("first_sent", False)),
            "final_sent": bool(value.get("final_sent", False)),
            **{k: v for k, v in value.items() if k not in ("first_sent", "final_sent")},
        }
    return {"first_sent": False, "final_sent": False}


def kickoff_delta_minutes(kickoff):
    try:
        dt = datetime.strptime(kickoff, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        return (dt - datetime.now(TZ)).total_seconds() / 60.0
    except Exception:
        return None


def upcoming(kickoff):
    delta = kickoff_delta_minutes(kickoff)
    return delta is not None and 0 <= delta <= WINDOW_MINUTES


def send_serverchan(title, desp):
    key = os.environ["SERVERCHAN_SENDKEY"].strip()
    if not key:
        raise RuntimeError("SERVERCHAN_SENDKEY 未配置")
    r = requests.post(
        f"https://sctapi.ftqq.com/{key}.send",
        data={"title": title[:32], "desp": desp},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Server酱失败: {data}")
    return data


def send_test_push():
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    desp = (
        "### ✅ 半球假强监控测试成功\n\n"
        f"**时间**：{now}  \n"
        "**来源**：GitHub Actions  \n"
        "**用途**：验证 GitHub Secret → Server酱 → 微信 推送链路\n\n"
        "如果你在微信收到这条消息，说明推送链路正常。\n"
    )
    send_serverchan("✅ 半球假强监控测试成功", desp)
    print("TEST_PUSH_SUCCESS: Server酱测试消息已发送")


def message(m, r, alert_type, delta):
    reasons = "\n".join(f"- ✅ {x}" for x in r["reasons"])
    away_price = f'{r["away_half"]:.2f}' if r["away_half"] else "N/A"
    if alert_type == "final":
        heading = "### 🔥 半球假强临场最终确认"
        stage = f"开赛前约 {max(0, round(delta))} 分钟｜最后一次推送"
    else:
        heading = "### ⚠️ 半球假强首次预警"
        stage = f"首次命中｜距开赛约 {max(0, round(delta))} 分钟"

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

#### 命中条件
{reasons}

> 同一场比赛最多推送两次：首次命中一次；开赛前 {FINAL_WINDOW_MINUTES} 分钟内最终确认一次。
> 仅作赔率结构筛选，不保证赛果。

[打开 NowScore]({m.url})
"""


async def main():
    print("=" * 60)
    print("半球假强主队监控启动")
    print("当前时间:", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
    print("检查未来分钟数:", WINDOW_MINUTES)
    print("临场最终推送窗口:", FINAL_WINDOW_MINUTES, "分钟")
    print("最低匹配阈值:", MIN_SCORE)
    print("测试推送模式:", TEST_PUSH)
    print("=" * 60)

    if TEST_PUSH:
        send_test_push()
        return

    state = load_state()
    ids = set(
        x.strip()
        for x in os.getenv("WATCH_MATCH_IDS", "").split(",")
        if x.strip().isdigit()
    )
    try:
        discovered = await discover_match_ids()
        ids.update(discovered)
        print(f"自动发现候选: {len(discovered)} 场")
    except Exception as e:
        print("发现比赛失败:", repr(e))

    print("候选比赛总数:", len(ids))
    parsed_count = upcoming_count = scored_count = pushed_count = 0
    first_push_count = final_push_count = 0
    changed = False

    for mid in sorted(ids):
        try:
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
                print(f"结果: 排除｜不在未来 0-{WINDOW_MINUTES} 分钟监控窗口内")
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

            if not (r["score"] >= MIN_SCORE and r["reliable"]):
                print("结果: 排除｜当前未达到推送阈值")
                continue

            match_state = normalize_match_state(state.get(mid))
            in_final_window = 0 <= delta <= FINAL_WINDOW_MINUTES

            if in_final_window:
                if match_state["final_sent"]:
                    print("结果: 符合｜临场最终提醒此前已推送，本轮不重复")
                    continue

                send_serverchan(
                    f"临场确认 {r['score']}/6｜{m.home} vs {m.away}",
                    message(m, r, "final", delta),
                )
                now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                match_state["final_sent"] = True
                match_state["final_sent_at"] = now
                # 如果第一次发现就已经进入临场窗口，只发“最终确认”这一条，
                # 同时把首次提醒视为已完成，避免同一轮/下一轮再补发首次提醒。
                if not match_state["first_sent"]:
                    match_state["first_sent"] = True
                    match_state["first_sent_at"] = now
                    match_state["first_sent_via"] = "final_window"
                match_state["kickoff"] = m.kickoff
                match_state["match"] = f"{m.home} vs {m.away}"
                state[mid] = match_state
                changed = True
                pushed_count += 1
                final_push_count += 1
                print("结果: 符合 → 已推送【临场最终确认】，以后不再推送本场")
                continue

            if match_state["first_sent"]:
                print(
                    f"结果: 符合｜首次提醒已推送，等待开赛前 {FINAL_WINDOW_MINUTES} 分钟内再做最终确认"
                )
                continue

            send_serverchan(
                f"首次预警 {r['score']}/6｜{m.home} vs {m.away}",
                message(m, r, "first", delta),
            )
            now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
            match_state["first_sent"] = True
            match_state["first_sent_at"] = now
            match_state["kickoff"] = m.kickoff
            match_state["match"] = f"{m.home} vs {m.away}"
            state[mid] = match_state
            changed = True
            pushed_count += 1
            first_push_count += 1
            print(
                f"结果: 符合 → 已推送【首次预警】；中间不重复，开赛前 {FINAL_WINDOW_MINUTES} 分钟内再确认一次"
            )

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
    print("首次预警推送:", first_push_count)
    print("临场最终推送:", final_push_count)
    print("本轮微信推送:", pushed_count)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
