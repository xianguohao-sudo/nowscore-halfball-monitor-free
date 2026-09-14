"""North-Single discovery v2: prefer unique time+final-score identity across Chinese-name translations."""
import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from backtest.north_single_validate import (
    HEADERS,
    fetch_nowscore_day,
    fetch_okooo_issue,
    normalize_team,
    team_similarity,
)


def _hard_match(north, now):
    """Return minute distance when date/time and final score identify a possible same match."""
    try:
        left = datetime.strptime(north.kickoff, "%Y-%m-%d %H:%M")
        right = datetime.strptime(now.kickoff, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    minutes = abs((left - right).total_seconds()) / 60.0
    if minutes > 15:
        return None
    if (north.home_score, north.away_score) != (now.home_score, now.away_score):
        return None
    return minutes


def _mapped_row(north, now, method, minutes, home_sim, away_sim):
    if method == "unique-time-score":
        confidence = 1.0
    else:
        confidence = (home_sim + away_sim) / 2.0
    return {
        "match_id": now.match_id,
        "home_score": now.home_score,
        "away_score": now.away_score,
        "match_date": north.kickoff[:10],
        "source": "okooo-north-single",
        "issue": north.issue,
        "seq": north.seq,
        "north_home": north.home,
        "north_away": north.away,
        "nowscore_home": now.home,
        "nowscore_away": now.away,
        "kickoff": now.kickoff,
        "mapping_method": method,
        "mapping_confidence": round(confidence, 4),
        "mapping_home_similarity": round(home_sim, 4),
        "mapping_away_similarity": round(away_sim, 4),
        "mapping_minutes": minutes,
    }


def map_matches_v2(north_matches, now_by_day):
    """Strict mapping hierarchy.

    1) Date + <=15 min kickoff + exact final score are hard gates.
    2) If those gates leave exactly one NowScore event, the match identity is
       unique independently of Chinese translation, so accept it.
    3) If multiple events remain, require both team names to agree and require
       a clear similarity gap over the runner-up.
    """
    mapped, unmatched, ambiguous = [], [], []
    method_counts = {}

    for north in north_matches:
        hard = []
        for now in now_by_day.get(north.kickoff[:10], []):
            minutes = _hard_match(north, now)
            if minutes is None:
                continue
            home_sim = team_similarity(north.home, now.home)
            away_sim = team_similarity(north.away, now.away)
            avg = (home_sim + away_sim) / 2.0
            hard.append((avg, home_sim, away_sim, minutes, now))

        if not hard:
            unmatched.append({"reason": "no_time_score_candidate", **asdict(north)})
            continue

        hard.sort(key=lambda x: x[0], reverse=True)
        if len(hard) == 1:
            avg, home_sim, away_sim, minutes, now = hard[0]
            # Exact final score plus a unique event inside a 15-minute window is
            # the cross-site identity key. Names are retained for audit only.
            mapped.append(_mapped_row(north, now, "unique-time-score", minutes, home_sim, away_sim))
            method_counts["unique-time-score"] = method_counts.get("unique-time-score", 0) + 1
            continue

        best = hard[0]
        second = hard[1]
        best_avg, home_sim, away_sim, minutes, now = best
        second_avg = second[0]
        if home_sim >= 0.55 and away_sim >= 0.55 and best_avg >= 0.72 and best_avg - second_avg >= 0.10:
            mapped.append(_mapped_row(north, now, "time-score-team", minutes, home_sim, away_sim))
            method_counts["time-score-team"] = method_counts.get("time-score-team", 0) + 1
        else:
            ambiguous.append({
                "north": asdict(north),
                "reason": "multiple_time_score_candidates",
                "best": asdict(now),
                "best_avg_similarity": round(best_avg, 4),
                "best_home_similarity": round(home_sim, 4),
                "best_away_similarity": round(away_sim, 4),
                "second_avg_similarity": round(second_avg, 4),
                "candidate_count": len(hard),
            })

    return mapped, unmatched, ambiguous, method_counts


async def discover_and_map_v2(issues, cutoff):
    from playwright.async_api import async_playwright

    north_matches = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=HEADERS["User-Agent"], locale="zh-CN")
        page = await context.new_page()
        try:
            for issue in issues:
                try:
                    north_matches.extend(await fetch_okooo_issue(page, issue))
                except Exception as exc:
                    print(f"[okooo {issue}] ERROR {exc!r}")

            dedup = {}
            for item in north_matches:
                try:
                    dt = datetime.strptime(item.kickoff, "%Y-%m-%d %H:%M")
                except ValueError:
                    continue
                if dt >= cutoff:
                    continue
                key = (
                    item.kickoff,
                    normalize_team(item.home),
                    normalize_team(item.away),
                    item.home_score,
                    item.away_score,
                )
                dedup[key] = item
            north_matches = sorted(dedup.values(), key=lambda x: x.kickoff)
            print(f"north_single unique completed before cutoff={len(north_matches)}")

            days = sorted({datetime.strptime(item.kickoff, "%Y-%m-%d %H:%M").date() for item in north_matches})
            now_by_day = {}
            for day in days:
                try:
                    now_by_day[day.isoformat()] = await fetch_nowscore_day(page, day)
                except Exception as exc:
                    print(f"[nowscore {day}] ERROR {exc!r}")
                    now_by_day[day.isoformat()] = []
        finally:
            await browser.close()

    return north_matches, map_matches_v2(north_matches, now_by_day)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--issues", default=",".join(str(value) for value in range(26077, 26093)))
    parser.add_argument("--cutoff", default="2026-09-05 00:00")
    parser.add_argument("--output", default="data/north_single_candidates.json")
    parser.add_argument("--diagnostics", default="data/north_single_mapping.json")
    parser.add_argument("--min-mapping-rate", type=float, default=50.0)
    args = parser.parse_args()

    issues = [value.strip() for value in args.issues.split(",") if value.strip()]
    cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d %H:%M")
    north_matches, result = asyncio.run(discover_and_map_v2(issues, cutoff))
    mapped, unmatched, ambiguous, method_counts = result

    output = Path(args.output)
    diagnostics = Path(args.diagnostics)
    output.parent.mkdir(parents=True, exist_ok=True)
    diagnostics.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(mapped, ensure_ascii=False, indent=2), encoding="utf-8")

    rate = len(mapped) / len(north_matches) * 100 if north_matches else 0.0
    diag = {
        "version": "v2-unique-time-score",
        "issues": issues,
        "cutoff": args.cutoff,
        "north_parsed": len(north_matches),
        "mapped": len(mapped),
        "mapping_rate": round(rate, 2),
        "mapping_methods": method_counts,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "north_matches": [asdict(item) for item in north_matches],
    }
    diagnostics.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"NORTH_SINGLE_MAPPING_V2 north={len(north_matches)} mapped={len(mapped)} "
        f"unmatched={len(unmatched)} ambiguous={len(ambiguous)} rate={rate:.2f}% methods={method_counts}"
    )
    for item in unmatched[:20]:
        print("UNMATCHED", json.dumps(item, ensure_ascii=False))
    for item in ambiguous[:10]:
        print("AMBIGUOUS", json.dumps(item, ensure_ascii=False))

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
