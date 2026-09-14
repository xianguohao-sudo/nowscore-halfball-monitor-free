"""Discover completed Nowscore matches for historical backtesting."""
import asyncio
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List, Optional

import requests
from bs4 import BeautifulSoup

from nowscore import BASE, HEADERS


@dataclass
class HistoricalMatch:
    match_id: str
    match_date: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None


def date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


async def _extract_rows(page, day: date):
    candidates = [
        f"{BASE}/schedule.aspx?f=ft2&date={day:%Y-%m-%d}",
        f"{BASE}/schedule.aspx?f=ft2&date={day:%Y%m%d}",
    ]
    best = []
    for url in candidates:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3500)
        rows = await page.locator("tr").evaluate_all(
            """(trs) => trs.map(tr => {
                const links = Array.from(tr.querySelectorAll('a'));
                const hrefs = links.map(a => a.href || '');
                let matchId = null;
                for (const href of hrefs) {
                    const m = href.match(/(?:odds\/match\/|analysis\/|MatchDetail\/|id=)(\d+)/i);
                    if (m) { matchId = m[1]; break; }
                }
                if (!matchId) return null;
                let score = null;
                const scoreNodes = Array.from(
                    tr.querySelectorAll('.score, [class*="score"], [id*="score"]')
                );
                for (const node of scoreNodes) {
                    const m = (node.textContent || '').trim().match(/^(\d{1,2})\s*[-:]\s*(\d{1,2})$/);
                    if (m) { score = [Number(m[1]), Number(m[2])]; break; }
                }
                if (!score) {
                    const tokens = (tr.innerText || '').split(/\s+/);
                    for (const token of tokens) {
                        const m = token.match(/^(\d{1,2})-(\d{1,2})$/);
                        if (m) { score = [Number(m[1]), Number(m[2])]; break; }
                    }
                }
                return {matchId, score};
            }).filter(Boolean)"""
        )
        if len(rows) > len(best):
            best = rows
        if best and any(item.get("score") for item in best):
            break
    return best


def fetch_final_score(match_id: str):
    """Conservative fallback: only read exact score nodes or the page title."""
    urls = [
        f"{BASE}/odds/match/{match_id}.htm",
        f"{BASE}/analysis/{match_id}cn.html",
    ]
    for url in urls:
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.text, "lxml")
            for node in soup.find_all(
                attrs={"class": re.compile(r"score|result", re.I)}
            ):
                match = re.fullmatch(
                    r"\s*(\d{1,2})\s*[-:]\s*(\d{1,2})\s*",
                    node.get_text(" ", strip=True),
                )
                if match:
                    return int(match.group(1)), int(match.group(2))
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            match = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", title)
            if match:
                return int(match.group(1)), int(match.group(2))
        except Exception:
            continue
    return None


async def discover_history(start: date, end: date) -> List[HistoricalMatch]:
    from playwright.async_api import async_playwright

    found = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=HEADERS["User-Agent"])
        try:
            for day in date_range(start, end):
                try:
                    rows = await _extract_rows(page, day)
                    for row in rows:
                        score = row.get("score")
                        found[row["matchId"]] = HistoricalMatch(
                            match_id=row["matchId"],
                            match_date=day.isoformat(),
                            home_score=score[0] if score else None,
                            away_score=score[1] if score else None,
                        )
                    print(f"[{day}] discovered={len(rows)} total_unique={len(found)}")
                except Exception as exc:
                    print(f"[{day}] discovery error: {exc!r}")
        finally:
            await browser.close()

    return sorted(found.values(), key=lambda item: (item.match_date, item.match_id))


def discover_history_sync(start: date, end: date):
    return asyncio.run(discover_history(start, end))
