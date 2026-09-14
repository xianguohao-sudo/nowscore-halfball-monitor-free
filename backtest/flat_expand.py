"""Expand recent schedule seeds through Nowscore analysis history links."""
import argparse
import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from nowscore import BASE, HEADERS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    recent = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    seeds = recent[args.shard_index::args.shard_count][:args.seeds]
    known = {row["match_id"] for row in recent}
    found = {}
    for seed in seeds:
        try:
            response = requests.get(
                f"{BASE}/analysis/{seed['match_id']}cn.html",
                headers=HEADERS, timeout=20
            )
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.text, "lxml")
            for anchor in soup.find_all("a"):
                href = anchor.get("href", "")
                match = re.search(r"(?:analysis/|odds/match\\.aspx\\?id=|MatchDetail/)(\\d{6,})", href, re.I)
                if not match or match.group(1) in known:
                    continue
                match_id = match.group(1)
                found[match_id] = {
                    "match_id": match_id, "home_score": None, "away_score": None,
                    "page_index": 50, "row_order": int(match_id),
                }
        except Exception as exc:
            print(f"seed {seed['match_id']} error: {exc!r}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(list(found.values()), ensure_ascii=False), encoding="utf-8")
    print(f"expanded seeds={len(seeds)} unique_old_matches={len(found)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
