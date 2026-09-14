"""Frozen live policy derived from the 500-match PH01-PH06 backtest (Run #19).

The classifier remains V1.0. This module only decides whether a classified live
match may notify, stay in shadow/watch mode, or be disabled.
"""

POLICY_VERSION = "PH-V1-500-R19"
SOURCE_RUN_ID = 34872776005
LIVE_MIN_SCORE = 7

CLASS_POLICY = {
    "PH01": {
        "name": "真强升盘",
        "status": "DROP",
        "enabled": False,
        "n": 83,
        "hit_rate": 45.78,
        "roi": 7.01,
        "holdout_hit_rate": 41.67,
        "holdout_roi": -0.06,
    },
    "PH02": {
        "name": "稳定实让",
        "status": "DROP",
        "enabled": False,
        "n": 40,
        "hit_rate": 42.50,
        "roi": 0.06,
        "holdout_hit_rate": 45.45,
        "holdout_roi": 1.41,
    },
    "PH03": {
        "name": "强队浅盘",
        "status": "INSUFFICIENT",
        "enabled": False,
        "n": 1,
        "hit_rate": 100.00,
        "roi": 47.50,
        "holdout_hit_rate": 100.00,
        "holdout_roi": 47.50,
    },
    "PH04": {
        "name": "降盘转弱",
        "status": "WATCH-B",
        "enabled": False,
        "n": 61,
        "hit_rate": 59.02,
        "roi": -0.25,
        "holdout_hit_rate": 78.95,
        "holdout_roi": 34.25,
        # Score >=9 is a pre-defined strength bucket. It is shadow-tracked only;
        # it must pass a new independent dataset before any live push is enabled.
        "research_score": 9,
    },
    "PH05": {
        "name": "欧亚背离",
        "status": "DROP",
        "enabled": False,
        "n": 75,
        "hit_rate": 53.33,
        "roi": -11.63,
        "holdout_hit_rate": 52.00,
        "holdout_roi": -16.31,
    },
    "PH06": {
        "name": "均势防平",
        "status": "INSUFFICIENT",
        "enabled": False,
        "n": 0,
        "hit_rate": 0.00,
        "roi": None,
        "holdout_hit_rate": 0.00,
        "holdout_roi": None,
    },
}


def action_for(result):
    """Return ignore / disabled / watch / push without changing classifier rules."""
    if not result.get("classified"):
        return "ignore"
    if int(result.get("score", 0)) < LIVE_MIN_SCORE:
        return "ignore"

    policy = CLASS_POLICY.get(result.get("class_id"))
    if not policy:
        return "disabled"
    if policy.get("enabled") and policy.get("status") in {"KEEP-S", "KEEP-A"}:
        return "push"
    if policy.get("status") == "WATCH-B":
        return "watch"
    return "disabled"


def is_research_candidate(result):
    """PH04 >=9 shadow candidate for the next independent validation only."""
    if not result.get("classified") or result.get("class_id") != "PH04":
        return False
    threshold = CLASS_POLICY["PH04"].get("research_score", 99)
    return int(result.get("score", 0)) >= int(threshold)
