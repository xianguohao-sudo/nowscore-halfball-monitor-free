"""Evaluate one small candidate shard to stay below per-runner source limits."""
import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from backtest.flat_backtest import evaluate_one, settle

FIELDS = [
    "match_id", "page_index", "row_order", "league", "kickoff", "home", "away",
    "home_score", "away_score", "class_id", "class_name", "direction", "score",
    "open_line", "now_line", "open_consensus", "now_consensus", "selected_water",
    "selected_x12_move", "other_x12_move", "selected_win", "draw",
    "selected_loss", "profit",
]


def make_row(historical, match, result):
    selected = result["direction"]
    selected_handicap = result["now_line"] if selected == "home" else -result["now_line"]
    hs, aws = historical["home_score"], historical["away_score"]
    return {
        "match_id": historical["match_id"],
        "page_index": historical["page_index"],
        "row_order": historical["row_order"],
        "league": match.league, "kickoff": match.kickoff,
        "home": match.home, "away": match.away,
        "home_score": hs, "away_score": aws,
        "class_id": result["class_id"], "class_name": result["class_name"],
        "direction": selected, "score": result["score"],
        "open_line": result["open_line"], "now_line": result["now_line"],
        "open_consensus": result["open_consensus"],
        "now_consensus": result["now_consensus"],
        "selected_water": result["selected_now_water"],
        "selected_x12_move": result["selected_x12_move"],
        "other_x12_move": result["other_x12_move"],
        "selected_win": hs > aws if selected == "home" else aws > hs,
        "draw": hs == aws,
        "selected_loss": hs < aws if selected == "home" else aws < hs,
        "profit": settle(
            selected, hs, aws, selected_handicap, result["selected_now_water"]
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--limit", type=int, default=55)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    chosen = candidates[args.shard_index::args.shard_count][:args.limit]
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(evaluate_one, item): item for item in chosen}
        for future in as_completed(futures):
            item = futures[future]
            try:
                historical, match, result = future.result()
                if result.get("classified"):
                    rows.append(make_row(historical, match, result))
            except Exception as exc:
                errors.append((item["match_id"], repr(exc)))
                if len(errors) <= 10:
                    print(f"ERROR {item['match_id']}: {exc!r}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"shard={args.shard_index}/{args.shard_count} requested={len(chosen)} "
        f"classified={len(rows)} errors={len(errors)} output={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
