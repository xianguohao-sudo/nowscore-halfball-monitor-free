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

# 不再只扫 NowScore 首页。首页会出现推荐/焦点赛事，曾导致只发现未来几天的英超。
# 以“近日赛程 + 赛程页”为主，覆盖当前日期及临近赛程。
DISCOVERY_URLS = [
    BASE + "/schedule.aspx?f=sc1",
    BASE + "/schedule.aspx?f=ft1",
]


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
            for t2 in links[i + 1:i + 10]:
                if t2 and not any(x in t2 for x in ["Image", "分析", "指数", "胜平负", "三合一"]):
                    away = t2.strip()
                    break
            break

    rows = []
    for tr in soup.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if len(cells) < 13 or cells[0] in ("最大值", "最小值", "公司", ""):
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

    return MatchOdds(
        str(match_id), league, kickoff,
        home or f"主队-{match_id}", away or f"客队-{match_id}", url, rows
    )


def _extract_match_ids(text: str):
    ids = set()
    patterns = [
        re.compile(r"/odds/match/(\d+)\.htm", re.I),
        re.compile(r"/MatchDetail/(\d+)\.html", re.I),
        re.compile(r"/analysis/(?:[^\"'<>/]+/)?(\d+)(?:cn)?\.html", re.I),
        re.compile(r"/analysis/(\d+)", re.I),
    ]
    for pat in patterns:
        ids.update(pat.findall(text or ""))
    return ids


async def discover_match_ids():
    """
    从 NowScore 赛程页发现比赛，而不是从首页的推荐/焦点区发现。
    同时读取 href 和渲染后的完整 HTML，避免动态页面只在 onclick/脚本里出现比赛链接。
    """
    from playwright.async_api import async_playwright

    all_ids = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent=HEADERS["User-Agent"],
            locale="zh-CN",
            viewport={"width": 1600, "height": 1200},
        )
        try:
            for url in DISCOVERY_URLS:
                source_ids = set()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(7000)

                    hrefs = await page.locator("a").evaluate_all(
                        "els => els.map(a => a.href || '').filter(Boolean)"
                    )
                    source_ids.update(_extract_match_ids("\n".join(hrefs)))

                    html = await page.content()
                    source_ids.update(_extract_match_ids(html))

                    # 部分赛程行把比赛 ID 放在元素 id/data-* 属性或 onclick 中。
                    attrs = await page.locator("[id], [onclick], [data-id], [data-matchid]").evaluate_all(
                        "els => els.map(e => [e.id || '', e.getAttribute('onclick') || '', "
                        "e.getAttribute('data-id') || '', e.getAttribute('data-matchid') || ''].join(' '))"
                    )
                    attr_text = "\n".join(attrs)
                    source_ids.update(_extract_match_ids(attr_text))

                    print(f"赛事发现源: {url} -> {len(source_ids)} 场")
                    all_ids.update(source_ids)
                except Exception as e:
                    print(f"赛事发现源失败: {url} -> {repr(e)}")
        finally:
            await browser.close()

    print(f"赛程页去重后候选: {len(all_ids)} 场")
    return sorted(all_ids, key=lambda x: int(x))
