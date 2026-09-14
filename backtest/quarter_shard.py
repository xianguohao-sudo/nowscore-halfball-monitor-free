"""Evaluate one shard of historical matches with frozen PH01-PH06 rules."""
import argparse
import csv
import json
from pathlib import Path

from backtest.pregame_nowscore import fetch_match_pregame
from nowscore import fetch_match
from quarter_classifier import evaluate_quarter, line_value


FIELDNAMES = [
    "match_id", "page_index", "row_order", "league", "kickoff", "home", "away",
    "home_score", "away_score", "class_id", "class_name", "flags", "direction",
    "favorite", "score", "open_line", "now_line", "open_consensus", "now_consensus",
    "selected_open_water", "selected_now_water", "selected_water_move",
    "selected_open_x12", "selected_now_x12", "selected_x12_move",
    "other_open_x12", "other_now_x12", "other_x12_move",
    "draw_open", "draw_now", "draw_move", "ou_now_line",
    "selected_handicap", "profit", "target_hit", "selected_win", "draw", "selected_loss",
]


def _water(value):
    if value is None:
        return None
    value = float(value)
    return value - 1.0 if value >= 1.20 else value


def settle_quarter(selected_handicap, goal_margin, water):
    """Profit per 1 unit stake using Hong-Kong water."""
    water = _water(water)
    if water is None:
        return None
    if abs(selected_handicap + 0.25) <= 0.01:
        if goal_margin > 0:
            return water
        if goal_margin == 0:
            return -0.5
        return -1.0
    if abs(selected_handicap - 0.25) <= 0.01:
        if goal_margin > 0:
            return water
        if goal_margin == 0:
            return water / 2.0
        return -1.0
    return None


def likely_closing_quarter(match):
    values = [
        line_value(row.ah_now_line)
        for row in match.rows
        if line_value(row.ah_now_line) is not None
    ]
    if not values:
        return False
    return sum(abs(abs(value) - 0.25) <= 0.01 for value in values) >= max(2, len(values) // 2)


def evaluate_item(item, companies=4):
    overview = fetch_match(item["match_id"])
    if not likely_closing_quarter(overview):
        return None

    match = fetch_match_pregame(item["match_id"], companies=companies)
    result = evaluate_quarter(match)
    if not result.get("classified"):
        return None

    direction = result["direction"]
    hs, aws = int(item["home_score"]), int(item["away_score"])
    goal_margin = hs - aws if direction == "home" else aws - hs
    selected_handicap = result["now_line"] if direction == "home" else -result["now_line"]
    profit = settle_quarter(selected_handicap, goal_margin, result["selected_now_water"])

    selected_win = goal_margin > 0
    draw = goal_margin == 0
    selected_loss = goal_margin < 0
    target_hit = selected_win if selected_handicap < 0 else not selected_loss

    return {
        "match_id": item["match_id"],
        "page_index": item.get("page_index", 0),
        "row_order": item.get("row_order", 0),
        "league": match.league,
        "kickoff": match.kickoff,
        "home": match.home,
        "away": match.away,
        "home_score": hs,
        "away_score": aws,
        "class_id": result["class_id"],
        "class_name": result["class_name"],
        "flags": ",".join(result["flags"]),
        "direction": direction,
        "favorite": result["favorite"],
        "score": result["score"],
        "open_line": result["open_line"],
        "now_line": result["now_line"],
        "open_consensus": result["open_consensus"],
        "now_consensus": result["now_consensus"],
        "selected_open_water": result["selected_open_water"],
        "selected_now_water": result["selected_now_water"],
        "selected_water_move": result["selected_water_move"],
        "selected_open_x12": result["selected_open_x12"],
        "selected_now_x12": result["selected_now_x12"],
        "selected_x12_move": result["selected_x12_move"],
        "other_open_x12": result["other_open_x12"],
        "other_now_x12": result["other_now_x12"],
        "other_x12_move": result["other_x12_move"],
        "draw_open": result["draw_open"],
        "draw_now": result["draw_now"],
        "draw_move": result["draw_move"],
        "ou_now_line": result["ou_now_line"],
        "selected_handicap": selected_handicap,
        "profit": profit,
        "target_hit": target_hit,
        "selected_win": selected_win,
        "draw": draw,
        "selected_loss": selected_loss,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="data/quarter_candidates.json")
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--companies", type=int, default=4)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    assigned = [
        item for index, item in enumerate(candidates)
        if index % args.shard_count == args.shard_index
    ][:args.limit]

    rows = []
    errors = []
    for position, item in enumerate(assigned, start=1):
        try:
            result = evaluate_item(item, companies=args.companies)
            if result is not None:
                rows.append(result)
                print(
                    f"[{position}/{len(assigned)}] {item['match_id']} "
                    f"{result['class_id']} score={result['score']}/10 "
                    f"profit={result['profit']}"
                )
            else:
                print(f"[{position}/{len(assigned)}] {item['match_id']} skip")
        except Exception as exc:
            errors.append((item["match_id"], repr(exc)))
            print(f"[{position}/{len(assigned)}] {item['match_id']} ERROR {exc!r}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    error_path = output.with_suffix(".errors.json")
    error_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote classified={len(rows)} errors={len(errors)} to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
