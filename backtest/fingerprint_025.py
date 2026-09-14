"""Fixed -0.25 fake-favourite fingerprint.

This module is intentionally independent from fingerprint.py, which is the live
-0.5 monitor.  Do not tune these thresholds while evaluating the first sample.
"""
from statistics import median

PREFERRED = ["36", "Crow", "易", "伟", "18", "威", "澳", "利", "盈", "明"]


def _compact(value):
    return (value or "").replace(" ", "").replace("－", "-").replace("—", "-")


def _quarter(value):
    return _compact(value) in {
        "平/半", "平半", "0.25", "-0.25", "0/0.5", "0.0/0.5", "主-0.25"
    }


def _half(value):
    return _compact(value) in {"半球", "半", "0.5", "-0.5", "主-0.5"}


def _three_quarter(value):
    return _compact(value) in {
        "半/一", "半一", "0.75", "-0.75", "0.5/1", "主-0.75"
    }


def _water(value):
    """Return Hong-Kong style water (0.86), accepting decimal odds (1.86)."""
    if value is None:
        return None
    value = float(value)
    return value - 1.0 if value >= 1.20 else value


def _majority(count, total):
    return count >= max(3, (total + 1) // 2)


def evaluate_025(match):
    selected = [
        row for row in match.rows
        if any(key.lower() in row.company.lower() for key in PREFERRED)
    ] or match.rows

    valid = [
        row for row in selected
        if row.x12_open_home is not None
        and row.x12_open_away is not None
        and row.x12_now_home is not None
        and row.x12_now_away is not None
    ]
    asian = [
        row for row in valid
        if row.ah_open_line and row.ah_now_line
        and row.ah_open_away is not None and row.ah_now_away is not None
    ]

    open_quarter = [row for row in asian if _quarter(row.ah_open_line)]
    now_half_or_more = [
        row for row in asian
        if _half(row.ah_now_line) or _three_quarter(row.ah_now_line)
    ]

    required_quarter = _majority(len(open_quarter), len(asian))

    open_home = median([row.x12_open_home for row in valid]) if valid else None
    open_away = median([row.x12_open_away for row in valid]) if valid else None
    away_water_values = [
        _water(row.ah_open_away) for row in open_quarter
        if _water(row.ah_open_away) is not None
    ]
    away_water = median(away_water_values) if away_water_values else None

    home_moves = [
        row.x12_now_home - row.x12_open_home for row in valid
        if row.x12_open_home is not None and row.x12_now_home is not None
    ]
    home_move = median(home_moves) if home_moves else None

    retreats = [
        row for row in asian
        if _half(row.ah_open_line) and _quarter(row.ah_now_line)
    ]

    conditions = {
        "home_price_2_00_2_20": open_home is not None and 2.00 <= open_home <= 2.20,
        "away_price_2_55_3_10": open_away is not None and 2.55 <= open_away <= 3.10,
        "away_quarter_low_water": away_water is not None and away_water <= 0.90,
        "no_rise_to_half": not _majority(len(now_half_or_more), len(asian)),
        "retreat_half_to_quarter": len(retreats) >= max(1, len(asian) // 5),
        "home_price_not_compressed": home_move is not None and home_move >= -0.08,
    }
    score = sum(conditions.values())
    reliable = required_quarter and len(valid) >= 4 and len(asian) >= 3

    reasons = []
    labels = {
        "home_price_2_00_2_20": "初始主胜中位数处于2.00-2.20",
        "away_price_2_55_3_10": "初始客胜中位数处于2.55-3.10",
        "away_quarter_low_water": "客队+0.25初水不高于0.90",
        "no_rise_to_half": "临场未形成主让-0.5或更深共识",
        "retreat_half_to_quarter": "至少20%主流公司由-0.5退到-0.25",
        "home_price_not_compressed": "主胜未持续明显压低",
    }
    for key, passed in conditions.items():
        if passed:
            reasons.append(labels[key])

    representative = next(
        (
            row for key in ["36", "Crow", "18", "易", "伟"]
            for row in open_quarter if key.lower() in row.company.lower()
        ),
        open_quarter[0] if open_quarter else (valid[0] if valid else None),
    )

    return {
        "required_quarter": required_quarter,
        "reliable": reliable,
        "score": score,
        "conditions": conditions,
        "reasons": reasons,
        "companies": len(valid),
        "asian_companies": len(asian),
        "open_quarter_companies": len(open_quarter),
        "open_home": open_home,
        "open_away": open_away,
        "away_water": away_water,
        "home_move": home_move,
        "retreat_count": len(retreats),
        "representative": representative.company if representative else "N/A",
    }
