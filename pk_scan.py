"""独立的“平手假强客队（PKRH）”实时监控。

只共用 nowscore.py 的抓取层；不调用/修改原半球假强、平手6分类、平半模型。
"""
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from nowscore import discover_match_ids, fetch_match
from pk_fake_strong_away import evaluate_market, fetch_context, enrich_with_context

TZ = ZoneInfo("Asia/Shanghai")
WINDOW_MINUTES = int(os.getenv("PKRH_WINDOW_MINUTES", os.getenv("UPCOMING_WINDOW_MINUTES", "180")))
FINAL_WINDOW_MINUTES = int(os.getenv("PKRH_FINAL_WINDOW_MINUTES", "10"))
MIN_SCORE = int(os.getenv("PKRH_MIN_SCORE", "6"))
TEST_PUSH = os.getenv("PKRH_TEST_PUSH", "false").lower() == "true"
FORCE_EVAL = os.getenv("PKRH_FORCE_EVAL", "false").lower() == "true"
DRY_RUN = os.getenv("PKRH_DRY_RUN", "false").lower() == "true"
STATE = Path("data/pk_fake_strong_away_state.json")
STATE.parent.mkdir(exist_ok=True)


def load_state():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(data):
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_state(value):
    if isinstance(value, dict):
        return {
            "first_sent": bool(value.get("first_sent", False)),
            "final_sent": bool(value.get("final_sent", False)),
            **{k: v for k, v in value.items() if k not in ("first_sent", "final_sent")},
        }
    return {"first_sent": False, "final_sent": False}


def kickoff_delta(kickoff):
    try:
        dt = datetime.strptime(kickoff, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        return (dt - datetime.now(TZ)).total_seconds() / 60.0
    except Exception:
        return None


def in_window(delta):
    return delta is not None and 0 <= delta <= WINDOW_MINUTES


def send_serverchan(title, desp):
    if DRY_RUN:
        print("[DRY_RUN]", title)
        print(desp)
        return {"code": 0, "dry_run": True}
    key = os.environ["SERVERCHAN_SENDKEY"].strip()
    if not key:
        raise RuntimeError("SERVERCHAN_SENDKEY 未配置")
    response = requests.post(
        f"https://sctapi.ftqq.com/{key}.send",
        data={"title": title[:32], "desp": desp},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Server酱失败: {data}")
    return data


def test_push():
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    send_serverchan(
        "✅ PKRH平手假强客队监控测试",
        f"### ✅ 平手假强客队（PKRH）监控测试成功\n\n"
        f"**时间**：{now}  \n"
        f"**模型**：平手假强客队｜主胜反转  \n"
        f"**规则**：8项指纹，默认≥{MIN_SCORE}/8，盘口5项必须全部成立  \n"
        f"**隔离**：独立脚本、独立State、独立Workflow，不修改原半球假强模型。\n",
    )
    print("PKRH_TEST_PUSH_SUCCESS")


def _flag(value):
    return "✅" if value else "❌"


def analysis_url(match_id):
    return f"https://live.nowscore.com/analysis/{match_id}cn.html"


def message(match, result, alert_type, delta):
    conditions = result.get("conditions") or {}
    context = result.get("context") or {}
    home_recent = context.get("home_recent") or {}
    away_recent = context.get("away_recent") or {}
    home_venue = context.get("home_venue") or {}
    away_venue = context.get("away_venue") or {}
    h2h = context.get("h2h") or {}
    h2h_crow = context.get("h2h_crow") or {}

    stage = (
        f"开赛前约 {max(0, round(delta))} 分钟｜最终确认"
        if alert_type == "final"
        else f"首次命中｜距开赛约 {max(0, round(delta))} 分钟"
    )
    heading = "### 🔥 PKRH临场最终确认" if alert_type == "final" else "### 🚨 PKRH平手假强客队预警"

    checks = [
        ("Crow初盘平手", conditions.get("crow_open_pk")),
        ("Crow初盘客队低水", conditions.get("crow_open_away_low")),
        ("临场仍平手，不升客让平/半", conditions.get("stay_pk_no_away_rise")),
        ("Crow临场水位反转主低客高", conditions.get("crow_water_reverse_home")),
        ("欧赔主胜下降、客胜上升", conditions.get("x12_home_down_away_up")),
        ("客队近期状态更热", conditions.get("away_recent_hot")),
        ("同主/同客拆分支持主队", conditions.get("venue_split_support_home")),
        ("H2H Crow同初盘、终指方向反转确认", conditions.get("h2h_crow_reverse_confirm")),
    ]
    check_text = "\n".join(f"- {_flag(ok)} {name}" for name, ok in checks)

    home_move = result.get("home_move")
    away_move = result.get("away_move")
    home_move_text = "N/A" if home_move is None else f"{home_move:+.2f}"
    away_move_text = "N/A" if away_move is None else f"{away_move:+.2f}"

    return f"""{heading}

**比赛**：{match.home} vs {match.away}  
**联赛**：{match.league or 'N/A'}  
**开赛**：{match.kickoff}  
**阶段**：{stage}  
**模型**：**平手假强客队（PKRH）**  
**评分**：**{result.get('score', 0)}/8**  
**级别**：**{result.get('grade', 'N/A')}**  
**风险**：{result.get('risk', 'N/A')}  
**倾向**：**{result.get('lean', 'N/A')}**

#### 当前盘口
- Crow 初→即亚洲盘：`{result.get('crow_ah', 'N/A')}`
- Crow 初→即1X2：`{result.get('crow_x12', 'N/A')}`
- 主流欧赔主胜变化：`{home_move_text}`
- 主流欧赔客胜变化：`{away_move_text}`

#### 基本面确认
- 主队近期：{home_recent.get('summary', 'N/A')}
- 客队近期：{away_recent.get('summary', 'N/A')}
- 主队同主场：{home_venue.get('summary', 'N/A')}
- 客队同客场：{away_venue.get('summary', 'N/A')}
- 对战往绩：{h2h.get('summary', 'N/A')}
- 对战往绩Crow：{h2h_crow.get('summary', 'N/A')}

#### 8项指纹
{check_text}

> 硬门槛：前5项盘口结构必须全部成立；基本面/交锋只用于确认，不会把盘口不合格比赛“救活”。  
> 同一场最多两次提醒：首次命中 + 开赛前{FINAL_WINDOW_MINUTES}分钟内最终确认。  
> 仅作赔率结构筛选和历史规律研究，不保证赛果。

[打开赔率]({match.url})  
[打开近期战绩]({analysis_url(match.match_id)}#porlet_5)  
[打开对战往绩]({analysis_url(match.match_id)}#porlet_3)
"""


def mark_sent(state, match, alert_type):
    key = str(match.match_id)
    item = normalize_state(state.get(key))
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    if alert_type == "final":
        item["final_sent"] = True
        item["final_sent_at"] = now
        if not item["first_sent"]:
            item["first_sent"] = True
            item["first_sent_at"] = now
            item["first_sent_via"] = "final_window"
    else:
        item["first_sent"] = True
        item["first_sent_at"] = now
    item["kickoff"] = match.kickoff
    item["match"] = f"{match.home} vs {match.away}"
    state[key] = item


async def main():
    print("=" * 72)
    print("PKRH 平手假强客队｜主胜反转 独立监控启动")
    print("当前时间:", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
    print("监控窗口:", WINDOW_MINUTES, "分钟")
    print("最终确认窗口:", FINAL_WINDOW_MINUTES, "分钟")
    print("最低评分:", MIN_SCORE, "/8（前5项盘口结构必须全部通过）")
    print("隔离策略: pk_scan.py + pk_fake_strong_away.py + 独立State")
    print("=" * 72)

    if TEST_PUSH:
        test_push()
        return

    state = load_state()
    ids = set(x.strip() for x in os.getenv("PKRH_WATCH_MATCH_IDS", "").split(",") if x.strip().isdigit())
    try:
        found = await discover_match_ids()
        ids.update(found)
        print("自动发现:", len(found), "场")
    except Exception as exc:
        print("自动发现失败:", repr(exc))

    print("候选比赛:", len(ids), "场")
    changed = False
    parsed = upcoming = market_ready = qualified = first_push = final_push = 0

    for mid in sorted(ids, key=lambda x: int(x)):
        try:
            match = fetch_match(mid)
            parsed += 1
            delta = kickoff_delta(match.kickoff)
            print("-" * 72)
            print(f"[{mid}] {match.home} vs {match.away}｜{match.league or 'N/A'}｜{match.kickoff}")
            print("距离开赛:", "N/A" if delta is None else f"{delta:.1f}分钟")
            if not FORCE_EVAL and not in_window(delta):
                print(f"[PKRH] 排除：不在未来0-{WINDOW_MINUTES}分钟窗口")
                continue
            upcoming += 1

            market = evaluate_market(match)
            print(
                f"[PKRH] 盘口层 {market.get('market_score', market.get('score', 0))}/5"
                f"｜可靠={market.get('reliable')}｜ready={market.get('market_ready')}"
            )
            print("[PKRH] Crow:", market.get("crow_ah", "N/A"))
            if not market.get("market_ready"):
                print("[PKRH] 当前盘口未形成‘客强→主强’完整反转，不抓分析页")
                continue
            market_ready += 1

            context = fetch_context(match)
            result = enrich_with_context(market, context)
            print(
                f"[PKRH] 完整评分 {result.get('score', 0)}/8"
                f"｜级别={result.get('grade')}｜风险={result.get('risk')}｜倾向={result.get('lean')}"
            )
            print(
                "[PKRH] 近期:",
                (context.get("home_recent") or {}).get("summary"),
                "｜",
                (context.get("away_recent") or {}).get("summary"),
            )
            print("[PKRH] H2H Crow:", (context.get("h2h_crow") or {}).get("summary"))

            if not result.get("qualified") or result.get("score", 0) < MIN_SCORE:
                print("[PKRH] 未达到推送阈值")
                continue
            qualified += 1

            item = normalize_state(state.get(str(mid)))
            in_final = delta is not None and 0 <= delta <= FINAL_WINDOW_MINUTES
            if FORCE_EVAL:
                print("[PKRH] FORCE_EVAL：仅输出分析，不改变推送状态")
                continue
            if in_final:
                if item["final_sent"]:
                    print("[PKRH] 最终确认已推送，本轮不重复")
                else:
                    send_serverchan(
                        f"PKRH临场 {result['score']}/8｜{match.home} vs {match.away}",
                        message(match, result, "final", delta),
                    )
                    mark_sent(state, match, "final")
                    changed = True
                    final_push += 1
                    print("[PKRH] 已推送【临场最终确认】")
            elif item["first_sent"]:
                print(f"[PKRH] 首次已推送，等待开赛前{FINAL_WINDOW_MINUTES}分钟最终确认")
            else:
                send_serverchan(
                    f"PKRH预警 {result['score']}/8｜{match.home} vs {match.away}",
                    message(match, result, "first", delta),
                )
                mark_sent(state, match, "first")
                changed = True
                first_push += 1
                print("[PKRH] 已推送【首次预警】")
        except Exception as exc:
            print(f"[{mid}] ERROR:", repr(exc))

    if changed:
        save_state(state)

    print("=" * 72)
    print(
        f"汇总: parsed={parsed} upcoming={upcoming} market_ready={market_ready} "
        f"qualified={qualified} first_push={first_push} final_push={final_push}"
    )
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
