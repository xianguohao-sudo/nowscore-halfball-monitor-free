"""Discover enough completed Nowscore matches for the quarter-ball 500 backtest."""
import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from backtest.flat_backtest import discover_recent_pages
from nowscore import BASE, HEADERS


DATE_ROW_JS = r"""trs => trs.map(tr => {
    const text = (tr.innerText || '').trim();
    const hrefs = Array.from(tr.querySelectorAll('a')).map(a => a.href || '');
    let matchId = null;
    for (const href of hrefs) {
        const m = href.match(/(?:odds\/match\/|analysis\/|MatchDetail\/|id=)(\d+)/i);
        if (m) { matchId = m[1]; break; }
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


async def expand_by_date(seed_rows, min_candidates=4500, history_days=30):
    """Expand the candidate pool by exact dates without pre-filtering handicap.

    We intentionally collect all completed matches here. The historical schedule
    page's displayed handicap is not trusted as the closing line; the expensive
    shard stage later verifies the last PRE-MATCH ('即') 3-in-1 snapshot.
    """
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
                for order, row in enumerate(page_rows):
                    if row.get("score") is None:
                        continue
                    found.setdefault(
                        row["matchId"],
                        {
                            "match_id": row["matchId"],
                            "home_score": row["score"][0],
                            "away_score": row["score"][1],
                            "page_index": 1000 + days_back,
                            "row_order": order,
                            "match_date": day.isoformat(),
                        },
                    )
                print(
                    f"[discover date {day}] rows={len(page_rows)} "
                    f"unique_added={len(found) - before} total={len(found)}"
                )
        finally:
            await browser.close()

    return list(found.values())


async def discover(max_pages, min_candidates, history_days):
    recent = await discover_recent_pages(max_pages)
    print(f"recent schedule pool={len(recent)}")
    rows = await expand_by_date(
        recent,
        min_candidates=min_candidates,
        history_days=history_days,
    )
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
    if len(rows) < args.min_candidates:
        print(
            f"WARNING: candidate pool below target: {len(rows)} < {args.min_candidates}; "
            f"increase --history-days if classified samples remain below 500"
        )
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
