"""Frozen V1 classifier for closing quarter-ball (±0.25) matches.

Rules use PRE-MATCH Asian and 1X2 prices only. The six categories are mutually
exclusive by priority, while `flags` keeps overlapping signals for analysis.
Home-perspective handicap convention:
    -0.25 = home gives a quarter ball
    +0.25 = away gives a quarter ball (home receives a quarter)
"""
from statistics import median

CLASS_NAMES = {
    "PH01": "真强升盘",
    "PH02": "稳定实让",
    "PH03": "强队浅盘",
    "PH04": "降盘转弱",
    "PH05": "欧亚背离",
    "PH06": "均势防平",
}

PREFERRED = ["36", "Crow", "易", "伟", "18", "威", "澳", "利", "盈", "明"]


def _compact(value):
    return (value or "").replace(" ", "").replace("－", "-").replace("—", "-")


def line_value(value):
    """Return home-perspective Asian handicap as a signed float."""
    text = _compact(value)
    if not text:
        return None

    received = text.startswith("受")
    if received:
        text = text[1:]

    table = {
        "平手": 0.0, "平": 0.0, "0": 0.0, "0.0": 0.0,
        "平/半": 0.25, "平半": 0.25, "0.25": 0.25,
        "0/0.5": 0.25, "0.0/0.5": 0.25,
        "半球": 0.5, "半": 0.5, "0.5": 0.5,
        "半/一": 0.75, "半一": 0.75, "0.75": 0.75, "0.5/1": 0.75,
        "一球": 1.0, "一": 1.0, "1": 1.0, "1.0": 1.0,
    }
    if text in table:
        magnitude = table[text]
        return magnitude if received else -magnitude

    try:
        number = float(text)
        if text.startswith("+") or text.startswith("-"):
            return number
        return -abs(number)
    except ValueError:
        return None


def _water(value):
    if value is None:
        return None
    value = float(value)
    return value - 1.0 if value >= 1.20 else value


def _selected_rows(match):
    rows = [
        row for row in match.rows
        if any(key.lower() in row.company.lower() for key in PREFERRED)
    ]
    return rows or list(match.rows)


def _median(values):
    values = [value for value in values if value is not None]
    return median(values) if values else None


def _consensus(values, target, tolerance=0.01):
    valid = [value for value in values if value is not None]
    if not valid:
        return 0.0
    count = sum(abs(value - target) <= tolerance for value in valid)
    return count / len(valid)


def _side_values(row, side):
    if side == "home":
        return {
            "open_water": _water(row.ah_open_home),
            "now_water": _water(row.ah_now_home),
            "open_x12": row.x12_open_home,
            "now_x12": row.x12_now_home,
        }
    return {
        "open_water": _water(row.ah_open_away),
        "now_water": _water(row.ah_now_away),
        "open_x12": row.x12_open_away,
        "now_x12": row.x12_now_away,
    }


def evaluate_quarter(match):
    rows = _selected_rows(match)
    usable = []
    for row in rows:
        open_line = line_value(row.ah_open_line)
        now_line = line_value(row.ah_now_line)
        if (
            open_line is None or now_line is None
            or row.x12_open_home is None or row.x12_open_draw is None or row.x12_open_away is None
            or row.x12_now_home is None or row.x12_now_draw is None or row.x12_now_away is None
        ):
            continue
        usable.append((row, open_line, now_line))

    if len(usable) < 3:
        return {"classified": False, "reason": "insufficient_companies", "companies": len(usable)}

    open_lines = [item[1] for item in usable]
    now_lines = [item[2] for item in usable]
    open_line = _median(open_lines)
    now_line = _median(now_lines)
    if open_line is None or now_line is None or abs(abs(now_line) - 0.25) > 0.01:
        return {"classified": False, "reason": "closing_not_quarter", "companies": len(usable)}

    favorite = "home" if now_line < 0 else "away"
    underdog = "away" if favorite == "home" else "home"

    fav_open_x12 = _median([_side_values(row, favorite)["open_x12"] for row, _, _ in usable])
    fav_now_x12 = _median([_side_values(row, favorite)["now_x12"] for row, _, _ in usable])
    dog_open_x12 = _median([_side_values(row, underdog)["open_x12"] for row, _, _ in usable])
    dog_now_x12 = _median([_side_values(row, underdog)["now_x12"] for row, _, _ in usable])

    fav_open_water = _median([_side_values(row, favorite)["open_water"] for row, _, _ in usable])
    fav_now_water = _median([_side_values(row, favorite)["now_water"] for row, _, _ in usable])
    dog_open_water = _median([_side_values(row, underdog)["open_water"] for row, _, _ in usable])
    dog_now_water = _median([_side_values(row, underdog)["now_water"] for row, _, _ in usable])

    draw_open = _median([row.x12_open_draw for row, _, _ in usable])
    draw_now = _median([row.x12_now_draw for row, _, _ in usable])
    ou_now_line = _median([
        getattr(row, "ou_now_line_value", None)
        if getattr(row, "ou_now_line_value", None) is not None else None
        for row, _, _ in usable
    ])

    fav_move = None if fav_open_x12 is None or fav_now_x12 is None else fav_now_x12 - fav_open_x12
    dog_move = None if dog_open_x12 is None or dog_now_x12 is None else dog_now_x12 - dog_open_x12
    draw_move = None if draw_open is None or draw_now is None else draw_now - draw_open
    fav_water_move = None if fav_open_water is None or fav_now_water is None else fav_now_water - fav_open_water
    dog_water_move = None if dog_open_water is None or dog_now_water is None else dog_now_water - dog_open_water

    same_sign = open_line * now_line > 0
    stable_quarter = abs(abs(open_line) - 0.25) <= 0.01 and same_sign
    from_flat = abs(open_line) <= 0.01
    retreated_from_half = abs(open_line) >= 0.49 and (open_line * now_line > 0)
    strong_shallow = stable_quarter and fav_now_x12 is not None and fav_now_x12 <= 2.05
    euro_divergence = (
        fav_move is not None and dog_move is not None
        and fav_move >= 0.08 and dog_move <= -0.04
    )
    draw_compressed = (
        draw_now is not None and draw_now <= 3.10
        and (draw_move is None or draw_move <= 0.02)
    )
    low_total = ou_now_line is not None and ou_now_line <= 2.25

    flags = []
    if from_flat:
        flags.append("PH01")
    if stable_quarter:
        flags.append("PH02")
    if strong_shallow:
        flags.append("PH03")
    if retreated_from_half:
        flags.append("PH04")
    if euro_divergence:
        flags.append("PH05")
    if draw_compressed and (low_total or ou_now_line is None):
        flags.append("PH06")

    if retreated_from_half:
        class_id = "PH04"
    elif from_flat:
        class_id = "PH01"
    elif strong_shallow:
        class_id = "PH03"
    elif euro_divergence:
        class_id = "PH05"
    elif draw_compressed and (low_total or ou_now_line is None):
        class_id = "PH06"
    elif stable_quarter:
        class_id = "PH02"
    else:
        return {
            "classified": False,
            "reason": "unsupported_quarter_trajectory",
            "companies": len(usable),
            "open_line": open_line,
            "now_line": now_line,
        }

    direction = favorite if class_id in {"PH01", "PH02"} else underdog
    selected = direction
    other = "away" if selected == "home" else "home"

    selected_open_x12 = _median([_side_values(row, selected)["open_x12"] for row, _, _ in usable])
    selected_now_x12 = _median([_side_values(row, selected)["now_x12"] for row, _, _ in usable])
    other_open_x12 = _median([_side_values(row, other)["open_x12"] for row, _, _ in usable])
    other_now_x12 = _median([_side_values(row, other)["now_x12"] for row, _, _ in usable])
    selected_open_water = _median([_side_values(row, selected)["open_water"] for row, _, _ in usable])
    selected_now_water = _median([_side_values(row, selected)["now_water"] for row, _, _ in usable])

    selected_x12_move = None if selected_open_x12 is None or selected_now_x12 is None else selected_now_x12 - selected_open_x12
    other_x12_move = None if other_open_x12 is None or other_now_x12 is None else other_now_x12 - other_open_x12
    selected_water_move = None if selected_open_water is None or selected_now_water is None else selected_now_water - selected_open_water

    line_points = {
        "PH01": 3, "PH02": 1, "PH03": 2,
        "PH04": 3, "PH05": 2, "PH06": 1,
    }[class_id]

    euro_points = 0
    if selected_x12_move is not None and other_x12_move is not None:
        if selected_x12_move <= -0.06 and other_x12_move >= 0.05:
            euro_points = 2
        elif selected_x12_move <= -0.03 or other_x12_move >= 0.05:
            euro_points = 1

    water_point = int(
        selected_now_water is not None and (
            selected_now_water <= 0.90
            or (selected_water_move is not None and selected_water_move <= -0.05)
        )
    )

    mismatch_point = int(
        (class_id == "PH03")
        or (class_id == "PH04" and fav_now_x12 is not None and fav_now_x12 >= 2.00)
        or (class_id in {"PH01", "PH02"} and fav_now_x12 is not None and fav_now_x12 <= 2.30)
    )

    now_consensus = _consensus(now_lines, now_line)
    consensus_point = int(now_consensus >= 0.60)
    draw_point = int(class_id in {"PH03", "PH04", "PH05", "PH06"} and draw_compressed)

    resistance_point = 0
    if class_id in {"PH03", "PH04", "PH05", "PH06"}:
        resistance_point = int(
            dog_now_x12 is not None and (
                dog_now_x12 <= 3.40
                or (dog_move is not None and dog_move <= -0.05)
            )
        )
    else:
        resistance_point = int(
            dog_now_x12 is not None and (
                dog_now_x12 >= 3.20
                or (dog_move is not None and dog_move >= 0.05)
            )
        )

    score_parts = {
        "line": line_points,
        "euro": euro_points,
        "water": water_point,
        "mismatch": mismatch_point,
        "consensus": consensus_point,
        "draw_risk": draw_point,
        "resistance": resistance_point,
    }
    score = min(10, sum(score_parts.values()))

    return {
        "classified": True,
        "class_id": class_id,
        "class_name": CLASS_NAMES[class_id],
        "flags": flags,
        "direction": direction,
        "favorite": favorite,
        "underdog": underdog,
        "score": score,
        "score_parts": score_parts,
        "companies": len(usable),
        "open_line": open_line,
        "now_line": now_line,
        "open_consensus": _consensus(open_lines, open_line),
        "now_consensus": now_consensus,
        "selected_open_water": selected_open_water,
        "selected_now_water": selected_now_water,
        "selected_water_move": selected_water_move,
        "selected_open_x12": selected_open_x12,
        "selected_now_x12": selected_now_x12,
        "selected_x12_move": selected_x12_move,
        "other_open_x12": other_open_x12,
        "other_now_x12": other_now_x12,
        "other_x12_move": other_x12_move,
        "favorite_open_x12": fav_open_x12,
        "favorite_now_x12": fav_now_x12,
        "favorite_x12_move": fav_move,
        "underdog_open_x12": dog_open_x12,
        "underdog_now_x12": dog_now_x12,
        "underdog_x12_move": dog_move,
        "draw_open": draw_open,
        "draw_now": draw_now,
        "draw_move": draw_move,
        "ou_now_line": ou_now_line,
    }
