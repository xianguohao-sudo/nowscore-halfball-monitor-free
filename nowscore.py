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

# 正式赛事发现入口：NowScore 比分+走势 / 今日赛事页
# 这里展示的是当前赛事池（含未开场、进行中、已完场以及竞足/北单筛选），
# 不再从首页或未来赛程页抓“推荐/未来几天”的比赛。
DISCOVERY_URL = BASE + "/2in1.aspx"


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
    """只从明确的比赛链接/JS调用形态中提取比赛ID，避免把联赛ID等数字误当比赛。"""
    ids = set()
    patterns = [
        re.compile(r"/odds/match/(\d+)\.htm", re.I),
        re.compile(r"/MatchDetail/(\d+)\.html", re.I),
        re.compile(r"/analysis/(?:[^\"'<>/]+/)?(\d+)(?:cn)?\.html", re.I),
        re.compile(r"/analysis/(\d+)", re.I),
        re.compile(r"(?:matchid|match_id|scheduleid|sid)\s*[:=]\s*[\"']?(\d{6,10})", re.I),
        re.compile(r"(?:showOdds|odds|analysis|matchdetail)\s*\(\s*[\"']?(\d{6,10})", re.I),
    ]
    for pat in patterns:
        ids.update(pat.findall(text or ""))
    return ids


async def discover_match_ids():
    """
    从 https://live.nowscore.com/2in1.aspx 的动态今日赛事池发现比赛。
    2in1 页面比赛数据由 JS 动态载入，因此使用 Playwright 等待页面加载后，
    同时扫描 href、完整 DOM、onclick/data-* 属性中的比赛ID。
    """
    from playwright.async_api import async_playwright

    ids = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent=HEADERS["User-Agent"],
            locale="zh-CN",
            viewport={"width": 1600, "height": 1200},
        )
        try:
            print(f"赛事发现入口: {DISCOVERY_URL}")
            await page.goto(DISCOVERY_URL, wait_until="domcontentloaded", timeout=45000)

            # 页面提示“载入成功后自动刷新”，给动态比分/赛程脚本足够时间。
            await page.wait_for_timeout(10000)

            # 1) 所有链接
            hrefs = await page.locator("a").evaluate_all(
                "els => els.map(a => a.href || '').filter(Boolean)"
            )
            href_ids = _extract_match_ids("\n".join(hrefs))
            ids.update(href_ids)
            print(f"2in1 href发现: {len(href_ids)} 场")

            # 2) 动态渲染后的完整DOM
            html = await page.content()
            dom_ids = _extract_match_ids(html)
            ids.update(dom_ids)
            print(f"2in1 DOM发现: {len(dom_ids)} 场")

            # 3) 常见动态属性。NowScore 部分比赛行不直接放 href。
            attrs = await page.locator(
                "[id], [onclick], [href], [data-id], [data-matchid], [data-match-id], [data-sid]"
            ).evaluate_all(
                "els => els.map(e => ["
                "e.id || '', e.getAttribute('onclick') || '', e.getAttribute('href') || '', "
                "e.getAttribute('data-id') || '', e.getAttribute('data-matchid') || '', "
                "e.getAttribute('data-match-id') || '', e.getAttribute('data-sid') || ''"
                "].join(' '))"
            )
            attr_ids = _extract_match_ids("\n".join(attrs))
            ids.update(attr_ids)
            print(f"2in1 属性发现: {len(attr_ids)} 场")

            # 日志辅助判断页面是否真的加载到了今日赛事，而不是空壳。
            body_text = await page.locator("body").inner_text()
            print(f"2in1 页面文本长度: {len(body_text)}")
            print(f"2in1 去重后候选: {len(ids)} 场")
        finally:
            await browser.close()

    return sorted(ids, key=lambda x: int(x))
