"""Merge sharded evidence and perform the single chronological validation."""
import argparse
import csv
import json
from pathlib import Path

from backtest.flat_backtest import choose_and_validate, make_report
from backtest.flat_shard import FIELDS


def boolean(value):
    return str(value).lower() == "true"


def number(value, integer=False):
    if value in (None, "", "None"):
        return None
    return int(float(value)) if integer else float(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--target", type=int, default=500)
    parser.add_argument("--output-dir", default="data/flat_backtest")
    args = parser.parse_args()

    by_id = {}
    files = sorted(Path(args.input_root).rglob("shard_*.csv"))
    for path in files:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                for key in ("page_index", "row_order", "home_score", "away_score", "score"):
                    row[key] = number(row[key], integer=True)
                for key in (
                    "open_line", "now_line", "open_consensus", "now_consensus",
                    "selected_water", "selected_x12_move", "other_x12_move", "profit",
                ):
                    row[key] = number(row[key])
                for key in ("selected_win", "draw", "selected_loss"):
                    row[key] = boolean(row[key])
                by_id[row["match_id"]] = row

    rows = sorted(
        by_id.values(),
        key=lambda x: (-x["page_index"], x["row_order"], x["match_id"])
    )
    if len(rows) > args.target:
        rows = rows[-args.target:]
    decisions, split = choose_and_validate(rows)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    meta = {
        "discovered": "shared candidates", "parsed": len(by_id),
        "classified": len(rows), "target": args.target,
        "shard_files": len(files),
    }
    report = make_report(meta, rows, decisions, split, [])
    with (output / "flat_500.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (output / "report.md").write_text(report, encoding="utf-8")
    (output / "validated_rules.json").write_text(
        json.dumps({
            "generated_at": __import__("datetime").date.today().isoformat(),
            "sample_size": len(rows), "split_index": split, "rules": decisions,
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report)
    if len(rows) < args.target:
        print(f"ERROR: merged only {len(rows)} unique classified samples")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
