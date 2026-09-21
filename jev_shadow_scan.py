import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from fingerprint import evaluate
from jev_shadow import record_halfball_shadow
from nowscore import discover_match_ids, fetch_match

TZ = ZoneInfo("Asia/Shanghai")
WINDOW_MINUTES = int(os.getenv("UPCOMING_WINDOW_MINUTES", "180"))
FINAL_WINDOW_MINUTES = int(os.getenv("FINAL_ALERT_WINDOW_MINUTES", "10"))
MIN_SCORE = int(os.getenv("MIN_MATCH_COUNT", "4"))


def delta_minutes(kickoff):
    try:
        dt = datetime.strptime(kickoff, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        return (dt - datetime.now(TZ)).total_seconds() / 60.0
    except Exception:
        return None


async def main():
    print("=" * 68)
    print("V7 JEV Shadow 实盘验证启动｜只观察，不改变微信推荐")
    ids = set(x.strip() for x in os.getenv("WATCH_MATCH_IDS", "").split(",") if x.strip().isdigit())
    try:
        ids.update(await discover_match_ids())
    except Exception as e:
        print("[JEV Shadow] 发现比赛失败:", repr(e))

    qualified = called = ok = failed = 0
    for mid in sorted(ids, key=lambda x: int(x)):
        try:
            m = fetch_match(mid)
            delta = delta_minutes(m.kickoff)
            if delta is None or not (0 <= delta <= WINDOW_MINUTES):
                continue
            hb = evaluate(m)
            if hb.get("vetoed") or hb.get("score", 0) < MIN_SCORE or not hb.get("reliable"):
                continue
            qualified += 1
            stage = "final" if delta <= FINAL_WINDOW_MINUTES else "first"
            row = record_halfball_shadow(m, hb, stage)
            if row is None:
                continue
            called += 1
            if row.get("jev_status") == "OK":
                ok += 1
            else:
                failed += 1
                print(f"[JEV Shadow] {mid} API状态={row.get('jev_status')}；生产推荐不受影响")
        except Exception as e:
            failed += 1
            print(f"[JEV Shadow][{mid}] ERROR:", repr(e))

    print("-" * 68)
    print(f"JEV Shadow汇总｜生产候选{qualified}｜本轮新调用{called}｜OK{ok}｜失败{failed}")
    print("说明：JEV结果不否决、不新增、不修改任何微信推荐。")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
