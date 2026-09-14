"""Merge original and expanded candidate JSON artifacts."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = {}
    files = sorted(Path(args.root).rglob("*.json"))
    for path in files:
        for row in json.loads(path.read_text(encoding="utf-8")):
            rows.setdefault(row["match_id"], row)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(list(rows.values()), ensure_ascii=False), encoding="utf-8")
    print(f"candidate_files={len(files)} unique_candidates={len(rows)}")
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
