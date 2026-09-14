import re
import requests
from bs4 import BeautifulSoup
from models import BookmakerOdds, MatchOdds

BASE = "https://live.nowscore.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

def _f(s):
    try:
        return float(s.strip())
    except Exception:
        return None

def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def fetch_match(match_id: str) -> MatchOdds:
    url = f"{BASE}/odds/match/{match_id}.htm"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    text = _clean(soup.get_text(" ", strip=True))

    m = re.search(r"([^\s]+)\s+开赛时间：\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
    league = m.group(1) if m else ""
    kickoff = m.group(2) if m else ""

    home, away = "", ""
    links = [a.get_text(" ", strip=True) for a in soup.find_all("a")]
    for i, t in enumerate(links):
        if "(主)" in t or "（主）" in t:
            home = re.sub(r"[\(（]主[\)）]", "", t).strip()
            for t2 in links[i+1:i+10]:
                if t2 and not any(x in t2 for x in ["Image", "分析", "指数", "胜平负", "三合一"]):
                    away = t2.strip()
                    break
            break

    rows = []
    for tr in soup.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td","th"])]
        if len(cells) < 13 or cells[0] in ("最大值","最小值","公司",""):
            continue
        try:
            row = BookmakerOdds(
                company=cells[0],
                ah_open_home=_f(cells[1]), ah_open_line=cells[2] or None, ah_open_away=_f(cells[3]),
                ah_now_home=_f(cells[4]), ah_now_line=cells[5] or None, ah_now_away=_f(cells[6]),
                x12_open_home=_f(cells[7]), x12_open_draw=_f(cells[8]), x12_open_away=_f(cells[9]),
                x12_now_home=_f(cells[10]), x12_now_draw=_f(cells[11]), x12_now_away=_f(cells[12]),
            )
            if row.x12_now_home and row.x12_now_draw and row.x12_now_away:
                rows.append(row)
        except Exception:
            pass

    if not rows:
        raise RuntimeError(f"未解析到赔率表: {url}")

    return MatchOdds(str(match_id), league, kickoff,
                     home or f"主队-{match_id}", away or f"客队-{match_id}", url, rows)

async def discover_match_ids():
    from playwright.async_api import async_playwright
    ids = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=HEADERS["User-Agent"])
        try:
            await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(6000)
            hrefs = await page.locator("a").evaluate_all("els => els.map(a => a.href).filter(Boolean)")
            pats = [
                re.compile(r"/odds/match/(\d+)\.htm", re.I),
                re.compile(r"/MatchDetail/(\d+)\.html", re.I),
                re.compile(r"/analysis/(\d+)", re.I),
            ]
            for href in hrefs:
                for pat in pats:
                    m = pat.search(href)
                    if m:
                        ids.add(m.group(1))
                        break
        finally:
            await browser.close()
    return sorted(ids)
