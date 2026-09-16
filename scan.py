import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from nowscore import discover_match_ids, fetch_match
from fingerprint import evaluate
from quarter_reversal import (
    evaluate_quarter_reversal,
    fetch_same_handicap_history,
    enrich_with_history,
)

TZ = ZoneInfo("Asia/Shanghai")
WINDOW_MINUTES = int(os.getenv("UPCOMING_WINDOW_MINUTES", "180"))
FINAL_WINDOW_MINUTES = int(os.getenv("FINAL_ALERT_WINDOW_MINUTES", "10"))
MIN_SCORE = int(os.getenv("MIN_MATCH_COUNT", "4"))
QR_MIN_SCORE = int(os.getenv("QUARTER_REVERSAL_MIN_SCORE", "5"))
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
        "### ✅ NowScore 多模型监控测试成功\n\n"
        f"**时间**：{now}  \n"
        "**来源**：GitHub Actions  \n"
        "**模型**：半球假强 + 客让平/半退平主胜反转  \n"
        "**频率**：计划每5分钟扫描；开赛前10分钟内做最终确认  \n\n"
        "如果你在微信收到这条消息，说明 GitHub Secret → Server酱 → 微信 推送链路正常。\n"
    )
    send_serverchan("✅ NowScore多模型监控测试成功", desp)
    print("TEST_PUSH_SUCCESS: Server酱测试消息已发送")


def halfball_message(m, r, alert_type, delta):
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

> 同一场比赛本模型最多推送两次：首次命中一次；开赛前 {FINAL_WINDOW_MINUTES} 分钟内最终确认一次。
> 仅作赔率结构筛选，不保证赛果。

[打开 NowScore]({m.url})
"""


def quarter_reversal_message(m, r, alert_type, delta):
    reasons = "\n".join(f"- ✅ {x}" for x in r.get("reasons", [])) or "- 无"
    history = r.get("history") or {}
    history_summary = history.get("summary", "历史同盘：N/A")
    history_note = ""
    if not history.get("available"):
        history_note = "（分析页暂未取到，不影响核心6项判断）"

    if alert_type == "final":
        heading = "### 🔥 客让平/半退平｜临场最终确认"
        stage = f"开赛前约 {max(0, round(delta))} 分钟｜最终结果"
    else:
        heading = "### 🚨 客让平/半退平｜主胜反转预警"
        stage = f"首次命中｜距开赛约 {max(0, round(delta))} 分钟"

    return f"""{heading}

**比赛**：{m.home} vs {m.away}  
**联赛**：{m.league or 'N/A'}  
**开赛**：{m.kickoff}  
**阶段**：{stage}  
**级别**：**{r.get('grade', 'N/A')}**  
**核心匹配**：{r.get('core_score', 0)}/6  
**历史加强**：+{r.get('history_point', 0)}/1  
**风险**：{r.get('risk', 'N/A')}  
**最终倾向**：**{r.get('lean', 'N/A')}**

- 代表公司：{r.get('rep', 'N/A')}
- 初→即 1X2：`{r.get('x12', 'N/A')}`
- 初→即 亚洲盘：`{r.get('ah', 'N/A')}`
- 主胜中位变化：`{r.get('home_move'):+.2f}`  
- 客胜中位变化：`{r.get('away_move'):+.2f}`
- 退盘共识：`{r.get('transition_count', 0)}/{r.get('companies', 0)} 家`
- {history_summary}{history_note}

#### 命中条件
{reasons}

> 规则核心：客队初始让平/半，但盘口退到平手，同时主胜明显下降、客胜明显抬升。
> “相同历史指数”只做加强项，不会因分析页抓取失败而漏掉核心信号。
> 同一场比赛本模型最多推送两次：首次命中一次；开赛前 {FINAL_WINDOW_MINUTES} 分钟内重新抓取赔率并最终确认一次。
> 仅作赔率结构筛选，不保证赛果。

[打开赔率]({m.url})  
[打开分析页](https://live.nowscore.com/analysis/{m.match_id}cn.html#porlet_3)
"""


def mark_sent(state, key, match, alert_type):
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    match_state = normalize_match_state(state.get(key))
    if alert_type == "final":
        match_state["final_sent"] = True
        match_state["final_sent_at"] = now
        if not match_state["first_sent"]:
            match_state["first_sent"] = True
            match_state["first_sent_at"] = now
            match_state["first_sent_via"] = "final_window"
    else:
        match_state["first_sent"] = True
        match_state["first_sent_at"] = now
    match_state["kickoff"] = match.kickoff
    match_state["match"] = f"{match.home} vs {match.away}"
    state[key] = match_state


async def main():
    print("=" * 68)
    print("NowScore 多模型盘口监控启动")
    print("当前时间:", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
    print("检查未来分钟数:", WINDOW_MINUTES)
    print("临场最终推送窗口:", FINAL_WINDOW_MINUTES, "分钟")
    print("半球假强最低匹配阈值:", MIN_SCORE)
    print("平半退平核心最低匹配阈值:", QR_MIN_SCORE, "/6")
    print("模型: 半球假强 + 客让平/半退平主胜反转")
    print("=" * 68)

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

    parsed_count = 0
    upcoming_count = 0
    changed = False

    hb_scored_count = hb_veto_count = 0
    hb_first_push_count = hb_final_push_count = 0

    qr_scored_count = qr_qualified_count = 0
    qr_first_push_count = qr_final_push_count = 0

    for mid in sorted(ids, key=lambda x: int(x)):
        try:
            m = fetch_match(mid)
            parsed_count += 1
            delta = kickoff_delta_minutes(m.kickoff)
            print("-" * 68)
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
            in_final_window = 0 <= delta <= FINAL_WINDOW_MINUTES

            # ============================================================
            # 模型1：半球假强主队
            # ============================================================
            hb = evaluate(m)
            hb_scored_count += 1
            print("[半球假强] "
                  f"评分 {hb['score']}/6｜可靠={hb['reliable']}｜风险={hb['risk']}｜倾向={hb['lean']}")
            print("[半球假强] 初→即 1X2:", hb["x12"])
            print("[半球假强] 初→即 亚洲盘:", hb["ah"])

            if hb.get("vetoed"):
                hb_veto_count += 1
                for reason in hb.get("vetoes", []):
                    print("[半球假强] 否决:", reason)
                print("[半球假强] VETO排除")
            elif hb["score"] >= MIN_SCORE and hb["reliable"]:
                hb_key = mid  # 保留旧状态key，兼容已有 data/state.json
                hb_state = normalize_match_state(state.get(hb_key))
                if in_final_window:
                    if hb_state["final_sent"]:
                        print("[半球假强] 临场最终提醒已推送，本轮不重复")
                    else:
                        send_serverchan(
                            f"临场确认 {hb['score']}/6｜{m.home} vs {m.away}",
                            halfball_message(m, hb, "final", delta),
                        )
                        mark_sent(state, hb_key, m, "final")
                        changed = True
                        hb_final_push_count += 1
                        print("[半球假强] 已推送【临场最终确认】")
                elif hb_state["first_sent"]:
                    print(f"[半球假强] 首次提醒已推送，等待开赛前 {FINAL_WINDOW_MINUTES} 分钟最终确认")
                else:
                    send_serverchan(
                        f"首次预警 {hb['score']}/6｜{m.home} vs {m.away}",
                        halfball_message(m, hb, "first", delta),
                    )
                    mark_sent(state, hb_key, m, "first")
                    changed = True
                    hb_first_push_count += 1
                    print("[半球假强] 已推送【首次预警】")
            else:
                print("[半球假强] 未达到推送阈值")

            # ============================================================
            # 模型2：客让平/半 -> 平手，欧赔主降客升 -> 主胜反转
            # ============================================================
            qr = evaluate_quarter_reversal(m)
            qr_scored_count += 1
            print(
                "[平半退平] "
                f"核心 {qr.get('core_score', 0)}/6｜可靠={qr.get('reliable')}｜"
                f"级别={qr.get('grade', 'N/A')}｜倾向={qr.get('lean', 'N/A')}"
            )
            if qr.get("reason"):
                print("[平半退平] 状态:", qr["reason"])
            if qr.get("x12"):
                print("[平半退平] 初→即 1X2:", qr["x12"])
            if qr.get("ah"):
                print("[平半退平] 初→即 亚洲盘:", qr["ah"])

            qr_is_qualified = (
                bool(qr.get("qualified"))
                and qr.get("core_score", 0) >= QR_MIN_SCORE
            )
            if qr_is_qualified:
                qr_qualified_count += 1
                qr_key = f"qr:{mid}"
                qr_state = normalize_match_state(state.get(qr_key))

                if in_final_window:
                    if qr_state["final_sent"]:
                        print("[平半退平] 临场最终提醒已推送，本轮不重复")
                    else:
                        # 只有真正要推送时才抓分析页，避免每5分钟重复请求历史页。
                        history = fetch_same_handicap_history(m)
                        qr_final = enrich_with_history(qr, history)
                        send_serverchan(
                            f"平半退平临场 {qr_final.get('grade')}｜{m.home} vs {m.away}",
                            quarter_reversal_message(m, qr_final, "final", delta),
                        )
                        mark_sent(state, qr_key, m, "final")
                        changed = True
                        qr_final_push_count += 1
                        print("[平半退平] 已推送【临场最终确认】", history.get("summary"))
                elif qr_state["first_sent"]:
                    print(f"[平半退平] 首次提醒已推送，等待开赛前 {FINAL_WINDOW_MINUTES} 分钟重新确认")
                else:
                    history = fetch_same_handicap_history(m)
                    qr_first = enrich_with_history(qr, history)
                    send_serverchan(
                        f"平半退平 {qr_first.get('grade')}｜{m.home} vs {m.away}",
                        quarter_reversal_message(m, qr_first, "first", delta),
                    )
                    mark_sent(state, qr_key, m, "first")
                    changed = True
                    qr_first_push_count += 1
                    print("[平半退平] 已推送【首次预警】", history.get("summary"))
            else:
                print("[平半退平] 未达到推送阈值")

        except Exception as e:
            print(f"[{mid}] ERROR:", repr(e))

    if changed:
        save_state(state)

    print("=" * 68)
    print("本轮扫描汇总")
    print("候选比赛:", len(ids))
    print("成功解析赔率:", parsed_count)
    print("进入时间窗口:", upcoming_count)
    print(
        "半球假强:",
        f"评分{hb_scored_count}｜VETO{hb_veto_count}｜首次{hb_first_push_count}｜临场{hb_final_push_count}",
    )
    print(
        "平半退平:",
        f"评分{qr_scored_count}｜命中{qr_qualified_count}｜首次{qr_first_push_count}｜临场{qr_final_push_count}",
    )
    print("本轮微信推送:", hb_first_push_count + hb_final_push_count + qr_first_push_count + qr_final_push_count)
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
