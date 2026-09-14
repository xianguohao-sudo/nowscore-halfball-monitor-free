"""Independent forward-validation ledger for PH04 research candidates.

This module never changes classifier thresholds and never enables live pushes.
It freezes PH04 >= 9 observations before kickoff, settles them only after the
final score is available, and reports hit-rate / ROI on future samples.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backtest.fetch_history import fetch_final_score

TZ = ZoneInfo("Asia/Shanghai")
LEDGER = Path("data/quarter_forward.json")
LEDGER.parent.mkdir(exist_ok=True)


def load_ledger():
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"version": 1, "matches": {}}
    except Exception:
        return {"version": 1, "matches": {}}


def save_ledger(data):
    LEDGER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _kickoff_dt(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def record_ph04_candidate(match, result, policy_version):
    """Freeze one pre-kickoff PH04>=9 observation. Returns True on new record."""
    if result.get("class_id") != "PH04" or int(result.get("score", 0)) < 9:
        return False

    ledger = load_ledger()
    matches = ledger.setdefault("matches", {})
    mid = str(match.match_id)
    if mid in matches:
        return False

    matches[mid] = {
        "match_id": mid,
        "league": match.league or "",
        "kickoff": match.kickoff or "",
        "home": match.home,
        "away": match.away,
        "direction": result.get("direction"),
        "score": int(result.get("score", 0)),
        "open_line": result.get("open_line"),
        "now_line": result.get("now_line"),
        "selected_now_water": result.get("selected_now_water"),
        "companies": int(result.get("companies", 0)),
        "policy_version": policy_version,
        "observed_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "status": "open",
        "home_score": None,
        "away_score": None,
        "target_hit": None,
        "profit": None,
    }
    save_ledger(ledger)
    return True


def _settle(direction, home_score, away_score, water):
    if direction == "home":
        selected, other = home_score, away_score
    else:
        selected, other = away_score, home_score

    if selected > other:
        return True, float(water)
    if selected == other:
        return True, float(water) / 2.0
    return False, -1.0


def settle_open_candidates(min_age_minutes=110):
    """Settle old open observations conservatively. Returns number newly settled."""
    ledger = load_ledger()
    matches = ledger.setdefault("matches", {})
    now = datetime.now(TZ)
    changed = 0

    for mid, item in matches.items():
        if item.get("status") != "open":
            continue
        kickoff = item.get("kickoff") or ""
        try:
            age = (now - _kickoff_dt(kickoff)).total_seconds() / 60.0
        except Exception:
            continue
        if age < min_age_minutes:
            continue

        score = fetch_final_score(mid)
        if not score:
            continue
        home_score, away_score = score
        water = item.get("selected_now_water")
        if water is None:
            continue

        hit, profit = _settle(
            item.get("direction"),
            int(home_score),
            int(away_score),
            float(water),
        )
        item.update({
            "status": "settled",
            "settled_at": now.isoformat(timespec="seconds"),
            "home_score": int(home_score),
            "away_score": int(away_score),
            "target_hit": bool(hit),
            "profit": round(float(profit), 4),
        })
        changed += 1

    if changed:
        save_ledger(ledger)
    return changed


def metrics():
    ledger = load_ledger()
    rows = [x for x in ledger.get("matches", {}).values() if x.get("status") == "settled"]
    n = len(rows)
    hits = sum(1 for x in rows if x.get("target_hit") is True)
    profit = sum(float(x.get("profit") or 0.0) for x in rows)
    hit_rate = (hits / n * 100.0) if n else 0.0
    roi = (profit / n * 100.0) if n else 0.0

    if n >= 80 and hit_rate >= 65.0 and roi > 5.0:
        review_status = "ELIGIBLE_KEEP_S_REVIEW"
    elif n >= 50 and hit_rate >= 60.0 and roi > 0.0:
        review_status = "ELIGIBLE_KEEP_A_REVIEW"
    else:
        review_status = "COLLECTING"

    return {
        "n": n,
        "hits": hits,
        "hit_rate": hit_rate,
        "profit": profit,
        "roi": roi,
        "review_status": review_status,
        "open": sum(1 for x in ledger.get("matches", {}).values() if x.get("status") == "open"),
    }
