"""North-Single discovery v3 with precision-oriented cross-source quality gates."""
import argparse
import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from backtest.north_single_discover_v2 import discover_and_map_v2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--issues", default=",".join(str(value) for value in range(26077, 26093)))
    parser.add_argument("--cutoff", default="2026-09-05 00:00")
    parser.add_argument("--output", default="data/north_single_candidates.json")
    parser.add_argument("--diagnostics", default="data/north_single_mapping.json")
    parser.add_argument("--min-mapped", type=int, default=500)
    parser.add_argument("--max-ambiguous-rate", type=float, default=10.0)
    parser.add_argument("--min-exact-time-rate", type=float, default=90.0)
    args = parser.parse_args()

    issues = [value.strip() for value in args.issues.split(",") if value.strip()]
    cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d %H:%M")
    north_matches, result = asyncio.run(discover_and_map_v2(issues, cutoff))
    mapped, unmatched, ambiguous, _ = result

    # Cross-site identity must be one-to-one. If two Okooo rows claim the same
    # NowScore match id, reject every claimant instead of picking one.
    by_id = defaultdict(list)
    for row in mapped:
        by_id[str(row["match_id"])].append(row)
    conflicts = []
    clean = []
    for match_id, claims in by_id.items():
        if len(claims) == 1:
            clean.append(claims[0])
        else:
            conflicts.append({"reason": "duplicate_target_match_id", "match_id": match_id, "claims": claims})

    clean.sort(key=lambda row: (row.get("kickoff", ""), row.get("match_id", "")))
    method_counts = Counter(row.get("mapping_method", "") for row in clean)
    exact_time = sum(float(row.get("mapping_minutes") or 0) == 0.0 for row in clean)
    exact_time_rate = exact_time / len(clean) * 100 if clean else 0.0
    ambiguous_total = len(ambiguous) + sum(len(item["claims"]) for item in conflicts)
    ambiguous_rate = ambiguous_total / len(north_matches) * 100 if north_matches else 100.0
    coverage_rate = len(clean) / len(north_matches) * 100 if north_matches else 0.0

    output = Path(args.output)
    diagnostics = Path(args.diagnostics)
    output.parent.mkdir(parents=True, exist_ok=True)
    diagnostics.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")

    diag = {
        "version": "v3-precision-gates",
        "issues": issues,
        "cutoff": args.cutoff,
        "north_parsed": len(north_matches),
        "mapped": len(clean),
        "coverage_rate": round(coverage_rate, 2),
        "mapping_rate": round(coverage_rate, 2),
        "mapping_methods": dict(method_counts),
        "exact_time_matches": exact_time,
        "exact_time_rate": round(exact_time_rate, 2),
        "ambiguous_count": len(ambiguous),
        "conflict_claims": ambiguous_total - len(ambiguous),
        "ambiguous_rate": round(ambiguous_rate, 2),
        "unmatched_count": len(unmatched),
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "conflicts": conflicts,
        "north_matches": [asdict(item) for item in north_matches],
        "quality_gates": {
            "min_mapped": args.min_mapped,
            "max_ambiguous_rate": args.max_ambiguous_rate,
            "min_exact_time_rate": args.min_exact_time_rate,
        },
    }
    diagnostics.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"NORTH_SINGLE_MAPPING_V3 north={len(north_matches)} mapped_unique={len(clean)} "
        f"coverage={coverage_rate:.2f}% unmatched_no_candidate={len(unmatched)} "
        f"ambiguous={len(ambiguous)} conflict_claims={ambiguous_total-len(ambiguous)} "
        f"ambiguous_rate={ambiguous_rate:.2f}% exact_time={exact_time_rate:.2f}% "
        f"methods={dict(method_counts)}"
    )
    for item in conflicts[:10]:
        print("CONFLICT", json.dumps(item, ensure_ascii=False))

    if len(clean) < args.min_mapped:
        print(f"FATAL: unique mapped intersection {len(clean)} < {args.min_mapped}")
        return 3
    if ambiguous_rate > args.max_ambiguous_rate:
        print(f"FATAL: ambiguous rate {ambiguous_rate:.2f}% > {args.max_ambiguous_rate:.2f}%")
        return 4
    if exact_time_rate < args.min_exact_time_rate:
        print(f"FATAL: exact-time rate {exact_time_rate:.2f}% < {args.min_exact_time_rate:.2f}%")
        return 5
    if len({row["match_id"] for row in clean}) != len(clean):
        print("FATAL: duplicate match ids survived one-to-one gate")
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
