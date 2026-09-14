"""Audit the saved 500-match artifact against independent match-result pages."""
import csv
import sys
from collections import defaultdict
from pathlib import Path

from backtest.fetch_history import fetch_final_score


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/audit/flat_500.csv")
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    groups = defaultdict(list)
    for row in rows:
        groups[row["class_id"]].append(row)

    checks = []
    for class_id in sorted(groups):
        group = groups[class_id]
        sample = group[:2] + group[-2:] if len(group) > 2 else group
        print(f"\n[{class_id}] total={len(group)}")
        for row in sample:
            recorded = (int(row["home_score"]), int(row["away_score"]))
            independent = fetch_final_score(row["match_id"])
            ok = independent is not None and independent == recorded
            checks.append(ok)
            print(
                f"{row['match_id']} {row['home']} vs {row['away']} "
                f"recorded={recorded[0]}-{recorded[1]} independent={independent} "
                f"class={row['class_id']} direction={row['direction']} "
                f"score={row['score']} audit={'OK' if ok else 'FAIL'}"
            )
    passed = sum(checks)
    print(f"\nAUDIT verified={passed}/{len(checks)}")
    return 0 if checks and passed == len(checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
