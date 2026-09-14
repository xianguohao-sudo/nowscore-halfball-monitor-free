import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from nowscore import discover_match_ids, fetch_match
from quarter_classifier import evaluate_quarter
from quarter_forward import metrics as forward_metrics
from quarter_forward import record_ph04_candidate, settle_open_candidates
from quarter_policy import (
    CLASS_POLICY,
    LIVE_MIN_SCORE,
    POLICY_VERSION,
    action_for,
    is_research_candidate,
)

TZ = ZoneInfo("Asia/Shanghai")
WINDOW_MINUTES = int(os.getenv("UPCOMING_WINDOW_MINUTES", "180"))
STATE = Path("data/quarter_state.json")
STATE.parent.mkdir(exist_ok=True)


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(data):
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    key = os.getenv("SERVERCHAN_SENDKEY", "").strip()
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


def _side_name(match, side):
    return match.home if side == "home" else match.away


def message(match, result):
    selected = _side_name(match, result["direction"])
    return f"""### 平/半盘验证通过\n\n**比赛**：{match.home} vs {match.away}  \n**联赛**：{match.league or 'N/A'}  \n**开赛**：{match.kickoff}  \n**分类**：{result['class_id']} {result['class_name']}  \n**评分**：{result['score']}/10  \n**方向**：{selected}  \n**策略版本**：{POLICY_VERSION}\n\n- 初盘中位：`{result['open_line']}`\n- 即盘中位：`{result['now_line']}`\n- 公司数：`{result['companies']}`\n- 当前策略：`{CLASS_POLICY[result['class_id']]['status']}`\n\n> 只有500场回测达到 KEEP-S / KEEP-A 的分类才允许微信推送。\n\n[打开 NowScore]({match.url})\n"""


async def main():
    print("=" * 60)
    print("平/半盘 PH01-PH06 实盘影子扫描")
    print("策略版本:", POLICY_VERSION)
    print("通知最低分:", LIVE_MIN_SCORE)
    print("当前允许推送分类:", [k for k, v in CLASS_POLICY.items() if v.get("enabled")])
    print("=" * 60)

    newly_settled = settle_open_candidates()
    if newly_settled:
        print(f"PH04独立前瞻：本轮新结算 {newly_settled} 场")

    state = load_state()
    ids = {x.strip() for x in os.getenv("WATCH_MATCH_IDS", "").split(",") if x.strip().isdigit()}
    try:
        discovered = await discover_match_ids()
        ids.update(discovered)
        print(f"自动发现候选: {len(discovered)} 场")
    except Exception as exc:
        print("发现比赛失败:", repr(exc))

    parsed = in_window = classified = watch_count = research_count = pushed = 0
    changed = False

    for match_id in sorted(ids):
        try:
            match = fetch_match(match_id)
            parsed += 1
            if not upcoming(match.kickoff):
                continue
            in_window += 1

            result = evaluate_quarter(match)
            if not result.get("classified"):
                continue
            classified += 1
            action = action_for(result)
            cid = result["class_id"]
            print(
                f"[{match_id}] {match.home} vs {match.away} "
                f"{cid} score={result['score']}/10 action={action}"
            )

            if is_research_candidate(result):
                research_count += 1
                is_new = record_ph04_candidate(match, result, POLICY_VERSION)
                if is_new:
                    print(f"[{match_id}] RESEARCH_CANDIDATE PH04>=9：已冻结进独立前瞻样本池，不推送")
                else:
                    print(f"[{match_id}] RESEARCH_CANDIDATE PH04>=9：已存在样本池，不重复记录")

            if action == "watch":
                watch_count += 1
                print(f"[{match_id}] WATCH-B：影子观察，不发微信")
                continue
            if action != "push":
                continue

            if state.get(match_id) == POLICY_VERSION:
                print(f"[{match_id}] 已按当前策略推送，跳过")
                continue
            send_serverchan(
                f"平/半 {cid} {result['score']}/10｜{match.home} vs {match.away}",
                message(match, result),
            )
            state[match_id] = POLICY_VERSION
            changed = True
            pushed += 1
            print(f"[{match_id}] KEEP规则命中 → 已推送微信")
        except Exception as exc:
            print(f"[{match_id}] ERROR:", repr(exc))

    if changed:
        save_state(state)

    fm = forward_metrics()
    print("=" * 60)
    print("本轮平/半扫描汇总")
    print("候选比赛:", len(ids))
    print("成功解析:", parsed)
    print("进入时间窗:", in_window)
    print("完成分类:", classified)
    print("WATCH-B:", watch_count)
    print("PH04>=9研究候选:", research_count)
    print("微信推送:", pushed)
    print("-" * 60)
    print("PH04>=9 独立前瞻验证")
    print("已结算样本:", fm["n"])
    print("待结算样本:", fm["open"])
    print(f"命中率: {fm['hit_rate']:.2f}%")
    print(f"ROI: {fm['roi']:.2f}%")
    print("升级状态:", fm["review_status"])
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
