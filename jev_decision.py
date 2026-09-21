import json
import os
import time
from typing import Any, Dict, Optional

import requests

JEV_URL = os.getenv("JEV_BASE_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.getenv("JEV_MODEL", "jev-latest")
JEV_TIMEOUT = float(os.getenv("JEV_TIMEOUT", "12"))
JEV_ENABLED = os.getenv("JEV_ENABLED", "false").lower() == "true"


def _answer_probability(answer: Any) -> Optional[float]:
    if isinstance(answer, (int, float)):
        return float(answer)
    if not isinstance(answer, dict):
        return None
    for key in ("probability", "prob", "value", "confidence", "score"):
        v = answer.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    for key in ("true", "yes"):
        v = answer.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _safe_json(r):
    try:
        return r.json()
    except Exception:
        return None


def evaluate_halfball(match, rule_result: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """JEV shadow decision. Never changes production recommendation by itself."""
    if not JEV_ENABLED:
        return {"enabled": False, "status": "DISABLED"}
    api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        return {"enabled": True, "status": "NO_API_KEY"}

    state = {
        "task": "football_halfball_false_strength_shadow_validation",
        "match": {
            "id": getattr(match, "match_id", None), "league": getattr(match, "league", None),
            "home": getattr(match, "home", None), "away": getattr(match, "away", None),
            "kickoff": getattr(match, "kickoff", None),
        },
        "rule_model": {
            "score": rule_result.get("score"), "risk": rule_result.get("risk"), "lean": rule_result.get("lean"),
            "representative_company": rule_result.get("rep"), "x12": rule_result.get("x12"),
            "asian_handicap": rule_result.get("ah"), "away_plus_half_decimal": rule_result.get("away_half"),
            "home_move": rule_result.get("home_move"), "away_move": rule_result.get("away_move"),
            "veto01": rule_result.get("veto01", False), "veto02": rule_result.get("veto02", False),
            "reasons": rule_result.get("reasons", []),
        },
        "context": context or rule_result.get("context") or {},
    }
    questions = {
        "false_strong_home": {"type": "noul", "instructions": "Does the structured pre-match market state indicate that the home side is falsely strong rather than genuinely strong? Judge only from supplied state.", "criteria": {"true": "False-strength home signal", "false": "Genuine/insufficient false-strength signal"}},
        "away_not_lose": {"type": "noul", "instructions": "Given only this pre-match state, is away-or-draw the better binary side than home win?", "criteria": {"true": "Away team does not lose", "false": "Home win is preferable"}},
        "strong_home_pressure": {"type": "noul", "instructions": "Does the market movement look like genuine strong pressure toward the home side that should warn against an away-not-lose recommendation?", "criteria": {"true": "Genuine home pressure warning", "false": "No strong genuine-home warning"}},
    }
    payload = {"model": JEV_MODEL, "state": state, "questions": questions}
    last_error = None
    last_diag = {}
    for attempt in range(3):
        try:
            r = requests.post(JEV_URL, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload, timeout=JEV_TIMEOUT)
            data = _safe_json(r)
            last_diag = {"http_status": r.status_code, "content_type": r.headers.get("content-type", ""), "response_json": data}
            if r.status_code in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt); continue
            if not r.ok:
                return {"enabled": True, "status": "HTTP_ERROR", "model": JEV_MODEL, **last_diag}
            if not isinstance(data, dict):
                return {"enabled": True, "status": "INVALID_JSON", "model": JEV_MODEL, **last_diag}
            answers = data.get("answers") or {}
            probs = {k: _answer_probability(answers.get(k)) for k in questions}
            missing = [k for k,v in probs.items() if v is None]
            if missing:
                return {"enabled": True, "status": "INVALID_ANSWERS", "model": JEV_MODEL, **probs, "missing_probabilities": missing, "raw_answers": answers, **last_diag}
            return {"enabled": True, "status": "OK", "model": JEV_MODEL, **probs, "raw_answers": answers, **last_diag}
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt < 2: time.sleep(2 ** attempt)
    return {"enabled": True, "status": "ERROR", "error": last_error, **last_diag}


def format_shadow_line(j: Dict[str, Any]) -> str:
    status = j.get("status", "UNKNOWN")
    if status != "OK": return f"JEV影子判断：{status}"
    def pct(v): return "N/A" if v is None else f"{v * 100:.1f}%"
    return "JEV影子判断：假强={}｜客队不败={}｜真实主强风险={}".format(pct(j.get("false_strong_home")), pct(j.get("away_not_lose")), pct(j.get("strong_home_pressure")))
