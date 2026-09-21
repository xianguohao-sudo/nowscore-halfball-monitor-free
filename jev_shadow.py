import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jev_decision import evaluate_halfball, format_shadow_line

TZ = ZoneInfo("Asia/Shanghai")
SHADOW_FILE = Path("data/jev_shadow.jsonl")
SHADOW_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read_keys():
    keys = set()
    if not SHADOW_FILE.exists():
        return keys
    try:
        for line in SHADOW_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            keys.add((str(row.get("match_id")), str(row.get("stage"))))
    except Exception as e:
        print("[JEV Shadow] 历史记录读取失败:", repr(e))
    return keys


def record_halfball_shadow(match, rule_result, stage):
    """Record JEV beside a production halfball candidate; never veto or create a recommendation."""
    key = (str(match.match_id), str(stage))
    if key in _read_keys():
        print(f"[JEV Shadow] {match.match_id} {stage} 已记录，本轮不重复调用")
        return None

    j = evaluate_halfball(match, rule_result)
    row = {
        "recorded_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "match_id": str(match.match_id),
        "match": f"{match.home} vs {match.away}",
        "league": match.league,
        "kickoff": match.kickoff,
        "stage": stage,
        "production_recommended": True,
        "rule_score": rule_result.get("score"),
        "rule_risk": rule_result.get("risk"),
        "rule_lean": rule_result.get("lean"),
        "rule_reliable": rule_result.get("reliable"),
        "rule_rep": rule_result.get("rep"),
        "rule_x12": rule_result.get("x12"),
        "rule_ah": rule_result.get("ah"),
        "rule_away_half": rule_result.get("away_half"),
        "rule_home_move": rule_result.get("home_move"),
        "rule_away_move": rule_result.get("away_move"),
        "rule_reasons": rule_result.get("reasons", []),
        "jev_status": j.get("status"),
        "jev_model": j.get("model"),
        "jev_false_strong_home": j.get("false_strong_home"),
        "jev_away_not_lose": j.get("away_not_lose"),
        "jev_strong_home_pressure": j.get("strong_home_pressure"),
        "jev_usage": j.get("usage") or {},
        "result_home": None,
        "result_away": None,
        "actual_away_not_lose": None,
        "settled": False,
    }
    with SHADOW_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print("[JEV Shadow]", format_shadow_line(j), "｜仅记录，不参与生产否决")
    return row
