"""Discover completed matches once and publish them for sharded workers."""
import argparse
import asyncio
import json
from pathlib import Path

from backtest.flat_backtest import discover_recent_pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=14)
    parser.add_argument("--output", default="data/flat_candidates.json")
    args = parser.parse_args()
    rows = asyncio.run(discover_recent_pages(args.max_pages))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(rows)} unique completed matches to {path}")
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
