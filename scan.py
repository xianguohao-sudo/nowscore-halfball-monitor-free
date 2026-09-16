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
        "**模型1**：半球假强主队  \n"
        "**模型2**：客让平/半退平主胜反转V2  \n"
        "**模型3**：平手假强客队（PKRH）  \n"
        "**频率**：每5分钟扫描；开赛前10分钟内做最终确认  \n\n"
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
    context = r.get("context") or {}
    home_recent = context.get("home_recent") or {}
    away_recent = context.get("away_recent") or {}
    h2h = context.get("h2h") or {}
    history_summary = history.get("summary", "历史同盘：N/A")

    if alert_type == "final":
        heading = "### 🔥 客让平/半退平V2｜临场最终确认"
        stage = f"开赛前约 {max(0, round(delta))} 分钟｜最终结果"
    else:
        heading = "### 🚨 客让平/半退平V2｜主胜反转预警"
        stage = f"首次命中｜距开赛约 {max(0, round(delta))} 分钟"

    hr = home_recent.get("summary", "主队近期：N/A")
    ar = away_recent.get("summary", "客队近期：N/A")
    hh = h2h.get("summary", "对战往绩：N/A")

    return f"""{heading}

**比赛**：{m.home} vs {m.away}  
**联赛**：{m.league or 'N/A'}  
**开赛**：{m.kickoff}  
**阶段**：{stage}  
**级别**：**{r.get('grade', 'N/A')}**  
**核心匹配**：{r.get('core_score', 0)}/6  
**BTI背离指数**：**{r.get('bti', 0)}/10**  
**客队热度确认**：{r.get('heat_points', 0)}/3  
**主队历史抵抗**：+{r.get('resistance_point', 0)}/1  
**历史同盘加强**：+{r.get('history_point', 0)}/1  
**风险**：{r.get('risk', 'N/A')}  
**最终倾向**：**{r.get('lean', 'N/A')}**

- 代表公司：{r.get('rep', 'N/A')}
- 初→即 1X2：`{r.get('x12', 'N/A')}`
- 初→即 亚洲盘：`{r.get('ah', 'N/A')}`
- 主胜中位变化：`{r.get('home_move'):+.2f}`  
- 客胜中位变化：`{r.get('away_move'):+.2f}`
- 退盘共识：`{r.get('transition_count', 0)}/{r.get('companies', 0)} 家`
- 主队近期：{hr}
- 客队近期：{ar}
- 对战往绩：{hh}
- {history_summary}

#### 命中条件
{reasons}

> V2规则：盘口核心必须先过关；近期战绩、对战往绩和历史同盘只负责确认/升级，不能把盘口不合格比赛救成推荐。
> BTI越高，代表“客队基本面越热，但市场反而越撤客”的背离越强。
> 同一场比赛最多推送两次：首次命中一次；开赛前 {FINAL_WINDOW_MINUTES} 分钟内重新抓取全部数据做最终确认一次。
> 仅作赔率结构筛选，不保证赛果。

[打开赔率]({m.url})  
[打开对战往绩](https://live.nowscore.com/analysis/{m.match_id}cn.html#porlet_3)  
[打开近期战绩](https://live.nowscore.com/analysis/{m.match_id}cn.html#porlet_6)
"""


def pkrh_message(m, r, alert_type, delta):
    conditions = r.get("conditions") or {}
    context = r.get("context") or {}
    hr = context.get("home_recent") or {}
    ar = context.get("away_recent") or {}
    hv = context.get("home_venue") or {}
    av = context.get("away_venue") or {}
    h2h = context.get("h2h") or {}
    h2h_crow = context.get("h2h_crow") or {}

    if alert_type == "final":
        heading = "### 🔥 平手假强客队PKRH｜临场最终确认"
        stage = f"开赛前约 {max(0, round(delta))} 分钟｜最终确认"
    else:
        heading = "### 🚨 平手假强客队PKRH｜主胜反转预警"
        stage = f"首次命中｜距开赛约 {max(0, round(delta))} 分钟"

    checks = [
        ("Crow初盘平手", conditions.get("crow_open_pk")),
        ("Crow初盘客队低水", conditions.get("crow_open_away_low")),
        ("临场仍平手，不升客让平/半", conditions.get("stay_pk_no_away_rise")),
        ("Crow临场主低客高反转", conditions.get("crow_water_reverse_home")),
        ("欧赔主降客升", conditions.get("x12_home_down_away_up")),
        ("客队近期状态更热", conditions.get("away_recent_hot")),
        ("同主/同客拆分支持主队", conditions.get("venue_split_support_home")),
        ("H2H Crow终指反转确认", conditions.get("h2h_crow_reverse_confirm")),
    ]
    check_text = "\n".join(f"- {'✅' if ok else '❌'} {name}" for name, ok in checks)
    home_move = r.get("home_move")
    away_move = r.get("away_move")
    home_move_text = "N/A" if home_move is None else f"{home_move:+.2f}"
    away_move_text = "N/A" if away_move is None else f"{away_move:+.2f}"

    return f"""{heading}

**比赛**：{m.home} vs {m.away}  
**联赛**：{m.league or 'N/A'}  
**开赛**：{m.kickoff}  
**阶段**：{stage}  
**评分**：**{r.get('score', 0)}/8**  
**级别**：**{r.get('grade', 'N/A')}**  
**风险**：{r.get('risk', 'N/A')}  
**倾向**：**{r.get('lean', 'N/A')}**

- Crow 初→即亚洲盘：`{r.get('crow_ah', 'N/A')}`
- Crow 初→即1X2：`{r.get('crow_x12', 'N/A')}`
- 主流欧赔主胜变化：`{home_move_text}`
- 主流欧赔客胜变化：`{away_move_text}`
- 主队近期：{hr.get('summary', 'N/A')}
- 客队近期：{ar.get('summary', 'N/A')}
- 主队同主场：{hv.get('summary', 'N/A')}
- 客队同客场：{av.get('summary', 'N/A')}
- 对战往绩：{h2h.get('summary', 'N/A')}
- 对战往绩Crow：{h2h_crow.get('summary', 'N/A')}

#### 8项指纹
{check_text}

> 前5项盘口结构是硬门槛；近期、主客场和对战往绩只负责确认，不会救活盘口不合格比赛。
> 同一场比赛本模型最多推送两次：首次命中一次；开赛前 {FINAL_WINDOW_MINUTES} 分钟内最终确认一次。
> 仅作赔率结构筛选，不保证赛果。

[打开赔率]({m.url})  
[打开对战往绩](https://live.nowscore.com/analysis/{m.match_id}cn.html#porlet_3)  
[打开近期战绩](https://live.nowscore.com/analysis/{m.match_id}cn.html#porlet_5)
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
    print("PKRH最低匹配阈值:", PKRH_MIN_SCORE, "/8")
    print("模型: 半球假强 + 客让平/半退平V2 + 平手假强客队PKRH")
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

    pkrh_scored_count = pkrh_market_ready_count = pkrh_qualified_count = 0
    pkrh_first_push_count = pkrh_final_push_count = 0

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

            hb = evaluate(m)
            hb_scored_count += 1
            print("[半球假强] " f"评分 {hb['score']}/6｜可靠={hb['reliable']}｜风险={hb['risk']}｜倾向={hb['lean']}")
            print("[半球假强] 初→即 1X2:", hb["x12"])
            print("[半球假强] 初→即 亚洲盘:", hb["ah"])

            if hb.get("vetoed"):
                hb_veto_count += 1
                for reason in hb.get("vetoes", []):
                    print("[半球假强] 否决:", reason)
                print("[半球假强] VETO排除")
            elif hb["score"] >= MIN_SCORE and hb["reliable"]:
                hb_key = mid
                hb_state = normalize_match_state(state.get(hb_key))
                if in_final_window:
                    if hb_state["final_sent"]:
                        print("[半球假强] 临场最终提醒已推送，本轮不重复")
                    else:
                        send_serverchan(f"临场确认 {hb['score']}/6｜{m.home} vs {m.away}", halfball_message(m, hb, "final", delta))
                        mark_sent(state, hb_key, m, "final")
                        changed = True
                        hb_final_push_count += 1
                        print("[半球假强] 已推送【临场最终确认】")
                elif hb_state["first_sent"]:
                    print(f"[半球假强] 首次提醒已推送，等待开赛前 {FINAL_WINDOW_MINUTES} 分钟最终确认")
                else:
                    send_serverchan(f"首次预警 {hb['score']}/6｜{m.home} vs {m.away}", halfball_message(m, hb, "first", delta))
                    mark_sent(state, hb_key, m, "first")
                    changed = True
                    hb_first_push_count += 1
                    print("[半球假强] 已推送【首次预警】")
            else:
                print("[半球假强] 未达到推送阈值")

            qr = evaluate_quarter_reversal(m)
            qr_scored_count += 1
            print("[平半退平V2] " f"核心 {qr.get('core_score', 0)}/6｜可靠={qr.get('reliable')}｜级别={qr.get('grade', 'N/A')}｜倾向={qr.get('lean', 'N/A')}")
            if qr.get("reason"):
                print("[平半退平V2] 状态:", qr["reason"])
            if qr.get("x12"):
                print("[平半退平V2] 初→即 1X2:", qr["x12"])
            if qr.get("ah"):
                print("[平半退平V2] 初→即 亚洲盘:", qr["ah"])

            qr_is_qualified = bool(qr.get("qualified")) and qr.get("core_score", 0) >= QR_MIN_SCORE
            if qr_is_qualified:
                qr_qualified_count += 1
                qr_key = f"qr:{mid}"
                qr_state = normalize_match_state(state.get(qr_key))

                if in_final_window:
                    if qr_state["final_sent"]:
                        print("[平半退平V2] 临场最终提醒已推送，本轮不重复")
                    else:
                        history = fetch_same_handicap_history(m)
                        context = fetch_form_h2h_context(m)
                        qr_final = enrich_with_context(qr, history, context)
                        print(f"[平半退平V2] BTI={qr_final.get('bti',0)}/10｜客热={qr_final.get('heat_points',0)}/3｜主场抵抗={qr_final.get('resistance_point',0)}/1")
                        send_serverchan(f"平半退平V2临场 {qr_final.get('grade')}｜{m.home} vs {m.away}", quarter_reversal_message(m, qr_final, "final", delta))
                        mark_sent(state, qr_key, m, "final")
                        changed = True
                        qr_final_push_count += 1
                        print("[平半退平V2] 已推送【临场最终确认】")
                elif qr_state["first_sent"]:
                    print(f"[平半退平V2] 首次提醒已推送，等待开赛前 {FINAL_WINDOW_MINUTES} 分钟重新确认")
                else:
                    history = fetch_same_handicap_history(m)
                    context = fetch_form_h2h_context(m)
                    qr_first = enrich_with_context(qr, history, context)
                    print(f"[平半退平V2] BTI={qr_first.get('bti',0)}/10｜客热={qr_first.get('heat_points',0)}/3｜主场抵抗={qr_first.get('resistance_point',0)}/1")
                    send_serverchan(f"平半退平V2 {qr_first.get('grade')}｜{m.home} vs {m.away}", quarter_reversal_message(m, qr_first, "first", delta))
                    mark_sent(state, qr_key, m, "first")
                    changed = True
                    qr_first_push_count += 1
                    print("[平半退平V2] 已推送【首次预警】")
            else:
                print("[平半退平V2] 未达到推送阈值")

            pkrh = evaluate_pkrh_market(m)
            pkrh_scored_count += 1
            print(
                "[PKRH平手假强客队] "
                f"盘口层 {pkrh.get('market_score', pkrh.get('score', 0))}/5｜"
                f"可靠={pkrh.get('reliable')}｜ready={pkrh.get('market_ready')}"
            )
            if pkrh.get("crow_ah"):
                print("[PKRH平手假强客队] Crow初→即亚洲盘:", pkrh.get("crow_ah"))

            if pkrh.get("market_ready"):
                pkrh_market_ready_count += 1
                pkrh_context = fetch_pkrh_context(m)
                pkrh_full = enrich_pkrh_context(pkrh, pkrh_context)
                print(
                    f"[PKRH平手假强客队] 完整评分 {pkrh_full.get('score', 0)}/8｜"
                    f"级别={pkrh_full.get('grade')}｜风险={pkrh_full.get('risk')}｜倾向={pkrh_full.get('lean')}"
                )
                print("[PKRH平手假强客队] H2H Crow:", (pkrh_context.get("h2h_crow") or {}).get("summary", "N/A"))

                pkrh_is_qualified = bool(pkrh_full.get("qualified")) and pkrh_full.get("score", 0) >= PKRH_MIN_SCORE
                if pkrh_is_qualified:
                    pkrh_qualified_count += 1
                    pkrh_key = f"pkrh:{mid}"
                    pkrh_state = normalize_match_state(state.get(pkrh_key))

                    if in_final_window:
                        if pkrh_state["final_sent"]:
                            print("[PKRH平手假强客队] 临场最终提醒已推送，本轮不重复")
                        else:
                            send_serverchan(
                                f"PKRH临场 {pkrh_full.get('score', 0)}/8｜{m.home} vs {m.away}",
                                pkrh_message(m, pkrh_full, "final", delta),
                            )
                            mark_sent(state, pkrh_key, m, "final")
                            changed = True
                            pkrh_final_push_count += 1
                            print("[PKRH平手假强客队] 已推送【临场最终确认】")
                    elif pkrh_state["first_sent"]:
                        print(f"[PKRH平手假强客队] 首次提醒已推送，等待开赛前 {FINAL_WINDOW_MINUTES} 分钟最终确认")
                    else:
                        send_serverchan(
                            f"PKRH预警 {pkrh_full.get('score', 0)}/8｜{m.home} vs {m.away}",
                            pkrh_message(m, pkrh_full, "first", delta),
                        )
                        mark_sent(state, pkrh_key, m, "first")
                        changed = True
                        pkrh_first_push_count += 1
                        print("[PKRH平手假强客队] 已推送【首次预警】")
                else:
                    print("[PKRH平手假强客队] 盘口成立，但基本面/交锋确认不足，未达到推送阈值")
            else:
                print("[PKRH平手假强客队] 未形成完整盘口反转，不抓分析页")

        except Exception as e:
            print(f"[{mid}] ERROR:", repr(e))

    if changed:
        save_state(state)

    print("=" * 68)
    print("本轮扫描汇总")
    print("候选比赛:", len(ids))
    print("成功解析赔率:", parsed_count)
    print("进入时间窗口:", upcoming_count)
    print("半球假强:", f"评分{hb_scored_count}｜VETO{hb_veto_count}｜首次{hb_first_push_count}｜临场{hb_final_push_count}")
    print("平半退平V2:", f"评分{qr_scored_count}｜命中{qr_qualified_count}｜首次{qr_first_push_count}｜临场{qr_final_push_count}")
    print("PKRH平手假强客队:", f"评分{pkrh_scored_count}｜盘口成型{pkrh_market_ready_count}｜命中{pkrh_qualified_count}｜首次{pkrh_first_push_count}｜临场{pkrh_final_push_count}")
    total_push = (
        hb_first_push_count + hb_final_push_count
        + qr_first_push_count + qr_final_push_count
        + pkrh_first_push_count + pkrh_final_push_count
    )
    print("本轮微信推送:", total_push)
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
