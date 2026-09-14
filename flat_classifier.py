"""Six fixed Asian-handicap classes involving the zero line.

The classifier uses only opening and latest/closing market data.  Match results
are deliberately not accepted by this module so the backtest cannot leak them.
Internal line convention: negative means the home team gives the handicap.
"""
import math
import re
from collections import Counter
from statistics import median

PREFERRED = ["36", "Crow", "易", "伟", "18", "威", "澳", "利", "盈", "明"]

CLASS_NAMES = {
    "F1": "原生均势型（平手→平手）",
    "F2": "主队暗强型（平手不动、主队受压）",
    "F3": "客队暗强型（平手不动、客队受压）",
    "F4": "升盘强化型（平手→平/半或更深）",
    "F5": "退盘削弱型（让球→平手）",
    "F6": "穿零反转型（让球方反转）",
}


def _compact(value):
    return (
        str(value or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("－", "-")
        .replace("—", "-")
        .replace("／", "/")
    )


def line_value(value):
    """Normalize a displayed handicap to the home-team handicap."""
    raw = _compact(value)
    if not raw:
        return None
    if raw in {"平手", "平", "0", "0.0", "0.00", "scratch", "pk"}:
        return 0.0

    receives = any(x in raw for x in ("受让", "受", "客让", "away-"))
    cleaned = raw.replace("受让", "").replace("受", "").replace("主让", "")
    cleaned = cleaned.replace("客让", "").replace("home", "").replace("away", "")

    names = [
        (("两球半/三", "2.5/3"), 2.75),
        (("两球/两球半", "2/2.5"), 2.25),
        (("球半/两", "1.5/2"), 1.75),
        (("一球/球半", "1/1.5"), 1.25),
        (("半球/一球", "半/一", "半一", "0.5/1"), 0.75),
        (("一球",), 1.0),
        (("球半",), 1.5),
        (("两球",), 2.0),
        (("平手/半球", "平/半", "平半", "0/0.5"), 0.25),
        (("半球", "半"), 0.5),
    ]
    magnitude = None
    for aliases, amount in names:
        if any(alias in cleaned for alias in aliases):
            magnitude = amount
            break
    if magnitude is None:
        numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", cleaned)
        if numbers:
            vals = [abs(float(number)) for number in numbers]
            magnitude = sum(vals) / len(vals)
    if magnitude is None or magnitude > 4:
        return None
    # Nowscore omits a sign when the home side gives; "受/客让" reverses it.
    return round(magnitude if receives else -magnitude, 2)


def _water(value):
    if value is None:
        return None
    value = float(value)
    return value - 1.0 if value >= 1.20 else value


def _med(values):
    values = [value for value in values if value is not None and math.isfinite(value)]
    return median(values) if values else None


def _mode(values):
    rounded = [round(value, 2) for value in values if value is not None]
    if not rounded:
        return None, 0, 0.0
    counts = Counter(rounded)
    value, count = counts.most_common(1)[0]
    return value, count, count / len(rounded)


def _side_value(home_value, away_value, side):
    return home_value if side == "home" else away_value


def _opposite(side):
    return "away" if side == "home" else "home"


def _favoured(line):
    if line is None or abs(line) < 0.01:
        return None
    return "home" if line < 0 else "away"


def _line_text(line):
    if line is None:
        return "N/A"
    if abs(line) < 0.01:
        return "0"
    side = "主" if line < 0 else "客"
    return f"{side}-{abs(line):.2f}"


def evaluate_flat(match):
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
        if line_value(row.ah_open_line) is not None
        and line_value(row.ah_now_line) is not None
        and row.ah_open_home is not None
        and row.ah_open_away is not None
        and row.ah_now_home is not None
        and row.ah_now_away is not None
    ]

    open_line, open_count, open_ratio = _mode(
        [line_value(row.ah_open_line) for row in asian]
    )
    now_line, now_count, now_ratio = _mode(
        [line_value(row.ah_now_line) for row in asian]
    )
    reliable = (
        len(valid) >= 4
        and len(asian) >= 3
        and open_ratio >= 0.50
        and now_ratio >= 0.50
    )
    if not reliable:
        return {"classified": False, "reliable": False, "reason": "盘口公司或共识不足"}

    open_home_water = _med([_water(row.ah_open_home) for row in asian])
    open_away_water = _med([_water(row.ah_open_away) for row in asian])
    now_home_water = _med([_water(row.ah_now_home) for row in asian])
    now_away_water = _med([_water(row.ah_now_away) for row in asian])

    open_home = _med([row.x12_open_home for row in valid])
    open_away = _med([row.x12_open_away for row in valid])
    now_home = _med([row.x12_now_home for row in valid])
    now_away = _med([row.x12_now_away for row in valid])
    home_move = None if open_home is None or now_home is None else now_home - open_home
    away_move = None if open_away is None or now_away is None else now_away - open_away
    home_water_move = (
        None if open_home_water is None or now_home_water is None
        else now_home_water - open_home_water
    )
    away_water_move = (
        None if open_away_water is None or now_away_water is None
        else now_away_water - open_away_water
    )

    zero_open = abs(open_line) < 0.01
    zero_now = abs(now_line) < 0.01
    class_id = None
    direction = None

    if not zero_open and not zero_now and open_line * now_line < 0:
        class_id = "F6"
        direction = _favoured(now_line)
    elif not zero_open and zero_now:
        class_id = "F5"
        direction = _opposite(_favoured(open_line))
    elif zero_open and not zero_now:
        class_id = "F4"
        direction = _favoured(now_line)
    elif zero_open and zero_now:
        home_pressure = sum([
            now_home_water is not None and now_away_water is not None
            and now_away_water - now_home_water >= 0.05,
            home_move is not None and home_move <= -0.03,
            away_move is not None and away_move >= 0.02,
        ])
        away_pressure = sum([
            now_home_water is not None and now_away_water is not None
            and now_home_water - now_away_water >= 0.05,
            away_move is not None and away_move <= -0.03,
            home_move is not None and home_move >= 0.02,
        ])
        if home_pressure >= 2 and home_pressure > away_pressure:
            class_id, direction = "F2", "home"
        elif away_pressure >= 2 and away_pressure > home_pressure:
            class_id, direction = "F3", "away"
        else:
            class_id = "F1"
            direction = "home" if (now_home or 99) <= (now_away or 99) else "away"

    if class_id is None:
        return {"classified": False, "reliable": True, "reason": "不涉及平手关键路径"}

    base = {"F1": 20, "F2": 35, "F3": 35, "F4": 40, "F5": 45, "F6": 50}[class_id]
    score = base
    reasons = [CLASS_NAMES[class_id]]

    if len(asian) >= 5:
        score += 10
        reasons.append(f"有效亚洲盘公司{len(asian)}家")
    if len(asian) >= 8:
        score += 5
    consensus = min(open_ratio, now_ratio)
    if consensus >= 0.70:
        score += 10
        reasons.append(f"初盘/即时共识度至少{consensus:.0%}")
    elif consensus >= 0.60:
        score += 6

    selected_now_water = _side_value(now_home_water, now_away_water, direction)
    selected_water_move = _side_value(home_water_move, away_water_move, direction)
    selected_x12_move = _side_value(home_move, away_move, direction)
    other_x12_move = _side_value(home_move, away_move, _opposite(direction))
    selected_now_x12 = _side_value(now_home, now_away, direction)
    other_now_x12 = _side_value(now_home, now_away, _opposite(direction))

    if selected_now_water is not None and selected_now_water <= 0.92:
        score += 10
        reasons.append(f"方向方即时水位{selected_now_water:.2f}≤0.92")
    if selected_water_move is not None and selected_water_move <= -0.04:
        score += 8
        reasons.append(f"方向方水位下降{selected_water_move:+.2f}")
    if selected_x12_move is not None and selected_x12_move <= -0.04:
        score += 10
        reasons.append(f"方向方胜赔下降{selected_x12_move:+.2f}")
    if other_x12_move is not None and other_x12_move >= 0.03:
        score += 7
        reasons.append(f"对手胜赔上升{other_x12_move:+.2f}")
    if (
        selected_now_x12 is not None and other_now_x12 is not None
        and selected_now_x12 + 0.08 <= other_now_x12
    ):
        score += 5
    if selected_now_water is not None and selected_now_water >= 1.03:
        score -= 10
        reasons.append("方向方处于高水，扣10分")
    if consensus < 0.60:
        score -= 8
        reasons.append("公司分歧较大，扣8分")
    if class_id == "F1":
        score = min(score, 59)
        reasons.append("原生均势无明确异动，最高59分，仅记录不推送")

    score = max(0, min(100, int(score)))
    return {
        "classified": True,
        "reliable": True,
        "class_id": class_id,
        "class_name": CLASS_NAMES[class_id],
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "companies": len(valid),
        "asian_companies": len(asian),
        "open_line": open_line,
        "now_line": now_line,
        "open_line_text": _line_text(open_line),
        "now_line_text": _line_text(now_line),
        "open_consensus": open_ratio,
        "now_consensus": now_ratio,
        "open_count": open_count,
        "now_count": now_count,
        "open_home_water": open_home_water,
        "open_away_water": open_away_water,
        "now_home_water": now_home_water,
        "now_away_water": now_away_water,
        "selected_now_water": selected_now_water,
        "home_move": home_move,
        "away_move": away_move,
        "selected_x12_move": selected_x12_move,
        "other_x12_move": other_x12_move,
        "open_home": open_home,
        "open_away": open_away,
        "now_home": now_home,
        "now_away": now_away,
    }
