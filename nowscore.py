import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
TZ = ZoneInfo("Asia/Shanghai")


def _f(s):
    try:
        return float(s.strip())
    except Exception:
        return None


def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _parse_match_header(soup):
    """从赔率页/比赛详情页头部提取联赛、开赛时间、主客队。"""
    text = _clean(soup.get_text(" ", strip=True))

    m = re.search(r"([^\s]+)\s+开赛时间：\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
    league = m.group(1) if m else ""
    kickoff = m.group(2) if m else ""

    home, away = "", ""
    links = [a.get_text(" ", strip=True) for a in soup.find_all("a")]
    for i, t in enumerate(links):
        if "(主)" in t or "（主）" in t:
            home = re.sub(r"[\(（]主[\)）]", "", t).strip()
            for t2 in links[i + 1:i + 12]:
                if t2 and not any(x in t2 for x in ["Image", "分析", "指数", "胜平负", "三合一", "简体", "繁体"]):
                    away = t2.strip()
                    break
            break

    # 某些特殊赛事详情页没有标准球队链接，使用 title 兜底。
    if not home or not away:
        title = _clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
        tm = re.search(r"(.+?)VS(.+?)(?:指数|分析|赔率|$)", title, re.I)
        if tm:
            home = home or tm.group(1).strip()
            away = away or tm.group(2).strip()

    return league, kickoff, home, away


def _fetch_detail_meta(match_id):
    """赔率页缺少比赛头部时，从 MatchDetail 页面补齐元数据；失败则返回空值。"""
    urls = [
        f"{BASE}/MatchDetail/{match_id}cn.html",
        f"{BASE}/matchdetail/{match_id}cn.html",
        f"{BASE}/MatchDetail/{match_id}.html",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            r.encoding = r.apparent_encoding or "utf-8"
            soup = BeautifulSoup(r.text, "lxml")
            league, kickoff, home, away = _parse_match_header(soup)
            if kickoff or home or away:
                return league, kickoff, home, away
        except Exception:
            pass
    return "", "", "", ""


def fetch_match(match_id: str) -> MatchOdds:
    url = f"{BASE}/odds/match/{match_id}.htm"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "lxml")

    league, kickoff, home, away = _parse_match_header(soup)

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

    # 特殊赛事赔率页可能只有赔率表，没有标准“开赛时间/球队名”头部。
    # 只有缺字段时才回退请求 MatchDetail，正常比赛不增加额外请求。
    if not kickoff or not home or not away:
        d_league, d_kickoff, d_home, d_away = _fetch_detail_meta(match_id)
        league = league or d_league
        kickoff = kickoff or d_kickoff
        home = home or d_home
        away = away or d_away

    return MatchOdds(
        str(match_id), league, kickoff,
        home or f"主队-{match_id}", away or f"客队-{match_id}", url, rows
    )


def _extract_match_ids(text: str):
    """
    从一条赛事行中只提取“主比赛ID”。

    旧实现会把 odds / MatchDetail / analysis / sid 等所有数字做并集，
    2in1 行里如果带辅助链接或其它组件ID，就会把它们误当成比赛。
    现在按可靠度分层：命中高优先级后立即返回，不再跨类型并集。
    """
    source = text or ""
    tiers = [
        # 最可靠：明确的比赛数据属性。故意不再接受模糊的 sid。
        [
            re.compile(r"(?:matchid|match_id|scheduleid)\s*[:=]\s*[\"']?(\d{6,10})", re.I),
            re.compile(r"data-(?:matchid|match-id|scheduleid)\s*=\s*[\"'](\d{6,10})[\"']", re.I),
        ],
        # 明确打开赔率/比赛详情的 JS 调用；不使用泛化 analysis(...)。
        [
            re.compile(r"(?:showOdds|odds|matchdetail)\s*\(\s*[\"']?(\d{6,10})", re.I),
        ],
        # 页面真实主链接。
        [
            re.compile(r"/odds/match/(\d+)\.htm", re.I),
        ],
        [
            re.compile(r"/MatchDetail/(\d+)(?:cn)?\.html", re.I),
            re.compile(r"/matchdetail/(\d+)(?:cn)?\.html", re.I),
        ],
        # analysis 只作为最后兜底，因为赛事行中最容易同时出现辅助分析链接。
        [
            re.compile(r"/analysis/(?:[^\"'<>/]+/)?(\d+)(?:cn)?\.html", re.I),
            re.compile(r"/analysis/(\d+)", re.I),
        ],
    ]

    for patterns in tiers:
        ids = set()
        for pat in patterns:
            ids.update(pat.findall(source))
        if ids:
            return ids
    return set()


def _is_finished_row(row_text: str) -> bool:
    """完场/无效/已经进行中的比赛都不进入赛前模型。"""
    tokens = [x.strip() for x in re.split(r"\s+", row_text or "") if x.strip()]
    stop_tokens = {
        "完", "完场", "取消", "中断", "腰斩", "延期",
        "上", "中", "下", "加", "点", "HT", "FT"
    }
    if any(t in stop_tokens for t in tokens):
        return True

    # 比分页进行中状态经常表现为 1、12、45+ 等分钟数；避免滚球进入赛前模型。
    for t in tokens:
        if re.fullmatch(r"\d{1,3}(?:\+\d{1,2})?'?", t):
            # 单独的 0/1 也可能来自比分或红牌，因此只作为弱信号，不直接排除。
            try:
                minute = int(re.match(r"\d+", t).group())
                if 2 <= minute <= 130:
                    return True
            except Exception:
                pass
    return False


def _extract_full_datetime(row_text: str, row_html: str):
    """优先从比赛行的文本/HTML中提取完整开赛时间。"""
    source = f"{row_text}\n{row_html}"
    patterns = [
        r"(20\d{2})[-/]([01]?\d)[-/]([0-3]?\d)[ T]([0-2]?\d):([0-5]\d)",
        r"(20\d{2})([01]\d)([0-3]\d)[ T]?([0-2]\d)([0-5]\d)",
    ]
    for pat in patterns:
        m = re.search(pat, source)
        if not m:
            continue
        try:
            y, mo, d, h, mi = map(int, m.groups())
            return datetime(y, mo, d, h, mi, tzinfo=TZ)
        except Exception:
            pass
    return None


def _infer_row_datetime(row_text: str, row_html: str, now: datetime):
    """
    推断 2in1 行的开赛时间。

    优先使用行内完整日期时间；若页面只显示 HH:MM，则按 NowScore 的足球比赛日
    习惯处理跨午夜：中午12点作为一天赛事池的分界。
    """
    full = _extract_full_datetime(row_text, row_html)
    if full:
        return full

    m = re.search(r"(?:^|\s)([0-2]?\d):([0-5]\d)(?:\s|$)", row_text or "")
    if not m:
        return None

    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23:
        return None

    # 2in1 的一个“比赛日”经常横跨午夜。
    # 例如当前 06:20：02:30 属于今天已结束，07:30 属于今天未来，21:00 属于昨天。
    if now.hour < 12:
        base_date = now.date() if h < 12 else (now - timedelta(days=1)).date()
    else:
        base_date = now.date() if h >= 12 else (now + timedelta(days=1)).date()

    return datetime(base_date.year, base_date.month, base_date.day, h, mi, tzinfo=TZ)


async def discover_match_ids():
    """
    只返回“当前时间 -> 未来 N 分钟”内、尚未开赛的比赛ID。

    这样历史比赛、已完场、进行中、未来几天的比赛，都不会再进入 fetch_match()，
    也不会出现在后面的赔率扫描日志里。
    """
    from playwright.async_api import async_playwright

    window_minutes = int(os.getenv("UPCOMING_WINDOW_MINUTES", "180"))
    now = datetime.now(TZ)
    active_ids = set()
    finished_ids = set()
    rows_with_match = 0
    invisible_rows = 0
    no_time_rows = 0
    outside_window_rows = 0
    in_window_rows = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent=HEADERS["User-Agent"],
            locale="zh-CN",
            viewport={"width": 1600, "height": 1200},
        )
        try:
            print(f"赛事发现入口: {DISCOVERY_URL}")
            print(f"赛事发现时间窗口: {now.strftime('%Y-%m-%d %H:%M')} -> +{window_minutes} 分钟")
            await page.goto(DISCOVERY_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(10000)

            trs = page.locator("tr")
            tr_count = await trs.count()
            print(f"2in1 表格行数: {tr_count}")

            for i in range(tr_count):
                tr = trs.nth(i)
                try:
                    # 页面中有大量隐藏的历史/未来模板行，只处理当前实际可见赛事。
                    if not await tr.is_visible():
                        invisible_rows += 1
                        continue
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

                kickoff = _infer_row_datetime(row_text, row_html, now)
                if kickoff is None:
                    no_time_rows += 1
                    continue

                delta = (kickoff - now).total_seconds() / 60.0
                if not (0 <= delta <= window_minutes):
                    outside_window_rows += 1
                    continue

                in_window_rows += 1
                active_ids.update(row_ids)

            active_ids.difference_update(finished_ids)

            body_text = await page.locator("body").inner_text()
            print(f"2in1 页面文本长度: {len(body_text)}")
            print(f"2in1 隐藏行跳过: {invisible_rows}")
            print(f"2in1 可见且含比赛ID行: {rows_with_match}")
            print(f"2in1 已完/进行中/无效排除: {len(finished_ids)} 场")
            print(f"2in1 无法解析时间行: {no_time_rows}")
            print(f"2in1 不在 0-{window_minutes} 分钟窗口: {outside_window_rows}")
            print(f"2in1 当前至未来 {window_minutes} 分钟候选: {len(active_ids)} 场")
        finally:
            await browser.close()

    return sorted(active_ids, key=lambda x: int(x))
