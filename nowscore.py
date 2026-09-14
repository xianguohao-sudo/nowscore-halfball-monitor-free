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
    """只从明确的比赛链接/JS调用形态中提取比赛ID。"""
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


def _is_finished_row(row_text: str) -> bool:
    """NowScore 2in1 状态列显示“完/完场”时直接排除，不再进入赔率抓取。"""
    # inner_text 保留单元格换行/制表符，因此按空白切 token 最稳。
    tokens = [x.strip() for x in re.split(r"\s+", row_text or "") if x.strip()]
    finished_tokens = {"完", "完场", "取消", "中断", "腰斩", "延期"}
    return any(t in finished_tokens for t in tokens)


async def discover_match_ids():
    """
    从 https://live.nowscore.com/2in1.aspx 当前页面的赛事行发现比赛。

    关键规则：
    1. 只从实际比赛行(tr)提取比赛 ID，避免页面脚本里的历史/缓存比赛混入。
    2. 状态列为“完/完场/取消/中断/腰斩/延期”的赛事在发现阶段直接排除。
    3. 未开场、即将开场以及仍在页面中的进行中赛事可以进入候选，后续再由
       scan.py 的未来 180 分钟时间窗口做第二层过滤。
    """
    from playwright.async_api import async_playwright

    active_ids = set()
    finished_ids = set()
    rows_with_match = 0

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
            await page.wait_for_timeout(10000)

            # 只扫描页面中的实际赛事行。这样不会把脚本缓存中的旧比赛一起抓进来。
            trs = page.locator("tr")
            tr_count = await trs.count()
            print(f"2in1 表格行数: {tr_count}")

            for i in range(tr_count):
                tr = trs.nth(i)
                try:
                    row_text = await tr.inner_text(timeout=2000)
                    row_html = await tr.evaluate("e => e.outerHTML")
                except Exception:
                    continue

                row_ids = _extract_match_ids(row_html)
                if not row_ids:
                    continue

                rows_with_match += 1
                if _is_finished_row(row_text):
                    finished_ids.update(row_ids)
                    continue

                active_ids.update(row_ids)

            # 若同一比赛意外同时出现在多个区域，完成状态优先排除。
            active_ids.difference_update(finished_ids)

            body_text = await page.locator("body").inner_text()
            print(f"2in1 页面文本长度: {len(body_text)}")
            print(f"2in1 含比赛ID行: {rows_with_match}")
            print(f"2in1 已完/无效赛事排除: {len(finished_ids)} 场")
            print(f"2in1 有效候选: {len(active_ids)} 场")
        finally:
            await browser.close()

    return sorted(active_ids, key=lambda x: int(x))
