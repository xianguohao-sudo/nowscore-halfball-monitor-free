"""Discover enough completed Nowscore matches for the quarter-ball 500 backtest."""
import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from backtest.flat_backtest import discover_recent_pages
from nowscore import BASE, HEADERS


MIN_MATCH_ID = 100000
MAX_MATCH_ID = 9_999_999_999


def plausible_match_id(value):
    text = str(value or "").strip()
    if not text.isdigit():
        return False
    number = int(text)
    return MIN_MATCH_ID <= number <= MAX_MATCH_ID


# Exact-date schedule rows expose the canonical event id directly on the row:
#   <tr id="tr1_2908783"> ... analysis(2908783) ... AsianOdds(2908783) ...
# Well-covered matches also expose a real 3-in-1 odds href such as:
#   Odds/3in1Odds.aspx?id=2908783&companyid=3
#
# A canonical row proves that the id is a real match; the explicit same-id
# 3in1Odds href proves that Nowscore has historical bookmaker rows worth
# querying.  This second gate intentionally removes obscure matches that are
# real events but repeatedly return "overview contains no bookmaker detail
# rows" during the four-company pre-match backtest.
DATE_ROW_JS = r"""trs => trs.map(tr => {
    const rowId = (tr.id || '').trim();
    const rowMatch = rowId.match(/^tr1_(\d{6,10})$/i);
    if (!rowMatch) return null;

    const matchId = rowMatch[1];
    const anchors = Array.from(tr.querySelectorAll('a'));
    const signals = anchors.map(a => [
        a.getAttribute('onclick') || '',
        a.getAttribute('href') || ''
    ].join(' ')).join(' ').toLowerCase();

    const id = String(matchId);
    const corroborated =
        signals.includes(`analysis(${id})`) ||
        signals.includes(`asianodds(${id})`) ||
        signals.includes(`europeodds(${id})`) ||
        signals.includes(`id=${id}&companyid=`) ||
        signals.includes(`id=${id}&`);
    if (!corroborated) return null;

    const has3in1 = anchors.some(a => {
        const href = (a.getAttribute('href') || '').trim();
        if (!/3in1odds\.aspx/i.test(href)) return false;
        try {
            const url = new URL(href, window.location.href);
            return url.searchParams.get('id') === id;
        } catch (_) {
            const lower = href.toLowerCase();
            return lower.includes(`id=${id}&`) ||
                   lower.endsWith(`id=${id}`);
        }
    });

    const text = (tr.innerText || '').trim();
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

    return {
        matchId,
        score,
        has3in1,
        hidden: ((tr.getAttribute('style') || '').toLowerCase().includes('display:none'))
    };
}).filter(Boolean)"""


def sanitize_rows(rows, source):
    """Reject impossible event ids before expensive odds fetching."""
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


async def expand_by_date(seed_rows, min_candidates=3200, history_days=45):
    """Expand candidate pool with odds-covered canonical exact-date rows.

    We intentionally do NOT pre-filter by the schedule-page handicap.  The date
    stage only verifies that the event is canonical and has a same-match 3-in-1
    odds link.  The shard stage still performs the strict test: four bookmaker
    PRE-MATCH ('即') snapshots are required and only then is a true 0.25 close
    classified by the frozen PH01-PH06 rules.
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
                completed = 0
                odds_covered_completed = 0
                without_3in1 = 0
                hidden_completed = 0
                duplicates = 0
                rejected_invalid = 0

                for order, row in enumerate(page_rows):
                    if row.get("score") is None:
                        continue
                    completed += 1
                    if row.get("hidden"):
                        hidden_completed += 1
                    if not row.get("has3in1"):
                        without_3in1 += 1
                        continue
                    odds_covered_completed += 1

                    match_id = str(row.get("matchId") or "").strip()
                    if not plausible_match_id(match_id):
                        rejected_invalid += 1
                        continue
                    if match_id in found:
                        duplicates += 1
                        continue

                    found[match_id] = {
                        "match_id": match_id,
                        "home_score": row["score"][0],
                        "away_score": row["score"][1],
                        "page_index": 1000 + days_back,
                        "row_order": order,
                        "match_date": day.isoformat(),
                        "source": "date-row-id",
                        "has_3in1": True,
                    }

                print(
                    f"[discover date {day}] canonical_rows={len(page_rows)} "
                    f"completed={completed} odds_covered={odds_covered_completed} "
                    f"without_3in1={without_3in1} hidden_completed={hidden_completed} "
                    f"duplicates={duplicates} rejected_invalid={rejected_invalid} "
                    f"unique_added={len(found) - before} total={len(found)}"
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
    parser.add_argument("--min-candidates", type=int, default=3200)
    parser.add_argument("--history-days", type=int, default=45)
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

    # Hard quality gate: never allow shards/merge to create a successful-looking
    # formal report from an undersized candidate universe.
    if len(rows) < args.min_candidates:
        print(
            f"FATAL: candidate pool below target: {len(rows)} < {args.min_candidates}; "
            f"increase --history-days or fix exact-date extraction"
        )
        return 3

    impossible = [row["match_id"] for row in rows if not plausible_match_id(row["match_id"])]
    if impossible:
        print(f"FATAL: invalid match IDs survived quality gate: {impossible[:10]}")
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
