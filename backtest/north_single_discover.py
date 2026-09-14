"""Discover and strictly map Okooo Beijing-Single matches to NowScore ids."""
import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from backtest.north_single_validate import discover_and_map


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--issues",
        default=",".join(str(value) for value in range(26077, 26093)),
        help="Comma-separated Okooo Beijing-Single issue ids.",
    )
    parser.add_argument("--cutoff", default="2026-09-05 00:00")
    parser.add_argument("--output", default="data/north_single_candidates.json")
    parser.add_argument("--diagnostics", default="data/north_single_mapping.json")
    parser.add_argument("--min-mapping-rate", type=float, default=50.0)
    args = parser.parse_args()

    issues = [value.strip() for value in args.issues.split(",") if value.strip()]
    cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d %H:%M")
    north_matches, mapping = asyncio.run(discover_and_map(issues, cutoff))
    mapped, unmatched, ambiguous = mapping

    output = Path(args.output)
    diagnostics = Path(args.diagnostics)
    output.parent.mkdir(parents=True, exist_ok=True)
    diagnostics.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(mapped, ensure_ascii=False, indent=2), encoding="utf-8")

    rate = len(mapped) / len(north_matches) * 100 if north_matches else 0.0
    diag = {
        "issues": issues,
        "cutoff": args.cutoff,
        "north_parsed": len(north_matches),
        "mapped": len(mapped),
        "mapping_rate": round(rate, 2),
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "north_matches": [asdict(item) for item in north_matches],
    }
    diagnostics.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"NORTH_SINGLE_MAPPING north={len(north_matches)} mapped={len(mapped)} "
        f"unmatched={len(unmatched)} ambiguous={len(ambiguous)} rate={rate:.2f}%"
    )

    print("NORTH_PARSED_SAMPLE:")
    for item in north_matches[:12]:
        print(json.dumps(asdict(item), ensure_ascii=False))
    print("UNMATCHED_SAMPLE:")
    for item in unmatched[:30]:
        print(json.dumps(item, ensure_ascii=False))
    print("AMBIGUOUS_SAMPLE:")
    for item in ambiguous[:12]:
        print(json.dumps(item, ensure_ascii=False))

    if not north_matches:
        print("FATAL: no completed Okooo Beijing-Single matches parsed")
        return 2
    if rate < args.min_mapping_rate:
        print(f"FATAL: mapping rate {rate:.2f}% < {args.min_mapping_rate:.2f}%")
        return 3
    if not mapped:
        print("FATAL: no unique high-confidence NowScore mappings")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
