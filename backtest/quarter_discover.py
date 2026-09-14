"""Discover enough completed Nowscore matches for the quarter-ball 500 backtest."""
import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from backtest.flat_backtest import discover_recent_pages
from nowscore import BASE, HEADERS


# Modern Nowscore match IDs are multi-digit event IDs.  A previous version used
# a bare ``id=(\d+)`` regex on every link in the schedule row; exact-date pages
# also contain league/category IDs (for example ``id=405``), which polluted the
# candidate pool and later produced thousands of false 0/4-company failures.
MIN_MATCH_ID = 100000
MAX_MATCH_ID = 9_999_999_999


def plausible_match_id(value):
    text = str(value or "").strip()
    if not text.isdigit():
        return False
    number = int(text)
    return MIN_MATCH_ID <= number <= MAX_MATCH_ID


DATE_ROW_JS = r"""trs => trs.map(tr => {
    const text = (tr.innerText || '').trim();
    const anchors = Array.from(tr.querySelectorAll('a'));

    // Only accept links whose PATH/QUERY explicitly identifies a match.
    // Never use a generic /id=(\d+)/ fallback: schedule rows also contain
    // league/category/filter IDs that are not match IDs.
    const patterns = [
        /\/odds\/match\/(\d{6,10})(?:\.html?|[\/?#]|$)/i,
        /\/odds\/match(?:\.aspx)?\?[^#]*\bid=(\d{6,10})(?:&|#|$)/i,
        /\/analysis\/(\d{6,10})(?:cn|sb)?(?:\.html?)?(?:[?#]|$)/i,
        /\/MatchDetail\/(\d{6,10})(?:[\/?#]|$)/i,
        /\/MatchDetail(?:\.aspx)?\?[^#]*\bid=(\d{6,10})(?:&|#|$)/i
    ];

    let matchId = null;
    for (const anchor of anchors) {
        const href = anchor.href || anchor.getAttribute('href') || '';
        for (const pattern of patterns) {
            const m = href.match(pattern);
            if (m) {
                matchId = m[1];
                break;
            }
        }
        if (matchId) break;
    }
    if (!matchId) return null;

    let score = null;
    for (const node of Array.from(tr.querySelectorAll('.score,[class*="score"],[id*="score"]'))) {
        const m = (node.textContent || '').trim().match(/^(\d{1,2})\s*[-:]\s*(\d{1,2})$/);
        if (m) { score = [Number(m[1]), Number(m[2])]; break; }
    }
    if (!score) {
        for (const token of text.split(/\s+/)) {
            const m = token.match(/^(\d{1,2})-(\d{1,2})$/);
            if (m) { score = [Number(m[1]), Number(m[2])]; break; }
        }
    }
    return {matchId, score};
}).filter(Boolean)"""


def sanitize_rows(rows, source):
    """Drop impossible IDs before expensive odds fetching and report them."""
    clean = []
    invalid = []
    for row in rows:
        match_id = row.get("match_id")
        if not plausible_match_id(match_id):
            invalid.append(str(match_id))
            continue
        copied = dict(row)
        copied["match_id"] = str(match_id)
        clean.append(copied)
    if invalid:
        sample = ",".join(invalid[:10])
        print(f"[quality {source}] rejected_invalid_ids={len(invalid)} sample={sample}")
    return clean


async def expand_by_date(seed_rows, min_candidates=4500, history_days=30):
    """Expand the candidate pool by exact dates without pre-filtering handicap.

    We intentionally collect all completed matches here. The historical schedule
    page's displayed handicap is not trusted as the closing line; the expensive
    shard stage later verifies the last PRE-MATCH ('即') 3-in-1 snapshot.
    """
    seed_rows = sanitize_rows(seed_rows, "recent")
    if len(seed_rows) >= min_candidates:
        return seed_rows

    found = {row["match_id"]: row for row in seed_rows}
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=HEADERS["User-Agent"])
        try:
            for days_back in range(0, history_days + 1):
                if len(found) >= min_candidates:
                    break
                day = date.today() - timedelta(days=days_back)
                try:
                    await page.goto(
                        f"{BASE}/schedule.aspx?date={day:%Y-%m-%d}",
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                    await page.wait_for_timeout(2000)
                    page_rows = await page.locator("tr").evaluate_all(DATE_ROW_JS)
                except Exception as exc:
                    print(f"[discover date {day}] ERROR {exc!r}")
                    continue

                before = len(found)
                rejected = 0
                completed = 0
                for order, row in enumerate(page_rows):
                    if row.get("score") is None:
                        continue
                    completed += 1
                    match_id = str(row.get("matchId") or "").strip()
                    if not plausible_match_id(match_id):
                        rejected += 1
                        continue
                    found.setdefault(
                        match_id,
                        {
                            "match_id": match_id,
                            "home_score": row["score"][0],
                            "away_score": row["score"][1],
                            "page_index": 1000 + days_back,
                            "row_order": order,
                            "match_date": day.isoformat(),
                            "source": "date",
                        },
                    )
                added = len(found) - before
                print(
                    f"[discover date {day}] rows={len(page_rows)} completed={completed} "
                    f"rejected_invalid={rejected} unique_added={added} total={len(found)}"
                )
        finally:
            await browser.close()

    return list(found.values())


async def discover(max_pages, min_candidates, history_days):
    recent = sanitize_rows(await discover_recent_pages(max_pages), "recent")
    print(f"recent schedule pool={len(recent)}")
    rows = await expand_by_date(
        recent,
        min_candidates=min_candidates,
        history_days=history_days,
    )
    rows = sanitize_rows(rows, "final")
    return sorted(rows, key=lambda x: (x.get("page_index", 0), x.get("row_order", 0)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--min-candidates", type=int, default=4500)
    parser.add_argument("--history-days", type=int, default=30)
    parser.add_argument("--output", default="data/quarter_candidates.json")
    args = parser.parse_args()

    rows = asyncio.run(discover(args.max_pages, args.min_candidates, args.history_days))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(
        f"wrote {len(rows)} unique completed matches to {path}; "
        f"target candidate pool={args.min_candidates}"
    )

    # Quality gate: never allow the workflow to continue to shards/merge with an
    # undersized pool.  A successful-looking report with far fewer than 500
    # classified samples is worse than an explicit discovery failure.
    if len(rows) < args.min_candidates:
        print(
            f"FATAL: candidate pool below target: {len(rows)} < {args.min_candidates}; "
            f"increase --history-days or fix schedule extraction before backtesting"
        )
        return 3

    impossible = [row["match_id"] for row in rows if not plausible_match_id(row["match_id"])]
    if impossible:
        print(f"FATAL: invalid match IDs survived quality gate: {impossible[:10]}")
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
