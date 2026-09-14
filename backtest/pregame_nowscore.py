"""Build historical MatchOdds with the last PRE-MATCH ('即') snapshot only."""
import re
import requests
from bs4 import BeautifulSoup

from models import BookmakerOdds, MatchOdds
from nowscore import BASE, HEADERS, _clean, _f

COMPANY_PRIORITY = ["36", "Crow", "澳", "威", "易", "伟", "明", "利", "盈", "18"]


def _goal_line_value(value):
    text = (value or "").strip().replace(" ", "")
    if not text:
        return None
    try:
        if "/" in text:
            parts = [float(part) for part in text.split("/") if part]
            return sum(parts) / len(parts) if parts else None
        return float(text)
    except ValueError:
        return None


def _detail_closing(match_id, company_id):
    url = f"{BASE}/odds/3in1Odds.aspx?companyid={company_id}&id={match_id}"
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "lxml")
    section = None
    asian = None
    x12 = None
    final_score = None
    goals = None
    for tr in soup.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if len(cells) >= 5 and cells[:5] == ["时", "比分", "主", "盘", "客"]:
            section = "asian"
            continue
        if len(cells) >= 5 and cells[:5] == ["时", "比分", "主", "和局", "客"]:
            section = "x12"
            continue
        if len(cells) >= 5 and cells[:5] == ["时", "比分", "大", "盘", "小"]:
            section = "goals"
            continue
        if len(cells) >= 7 and section == "asian" and cells[-1] == "滚" and final_score is None:
            score_match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", cells[1])
            if score_match:
                final_score = (int(score_match.group(1)), int(score_match.group(2)))
        if len(cells) < 7 or cells[-1] != "即":
            continue
        if section == "asian" and asian is None:
            asian = (_f(cells[2]), cells[3] or None, _f(cells[4]))
        elif section == "x12" and x12 is None:
            x12 = (_f(cells[2]), _f(cells[3]), _f(cells[4]))
        elif section == "goals" and goals is None:
            goals = (
                _f(cells[2]), cells[3] or None, _f(cells[4]),
                _goal_line_value(cells[3]),
            )

    if asian is None or x12 is None:
        raise RuntimeError(f"missing pre-match close: match={match_id} company={company_id}")
    return asian, x12, final_score, goals


def fetch_match_pregame(match_id, companies=4):
    url = f"{BASE}/odds/match/{match_id}.htm"
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "lxml")
    text = _clean(soup.get_text(" ", strip=True))

    header = re.search(r"([^\s]+)\s+开赛时间：\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
    league = header.group(1) if header else ""
    kickoff = header.group(2) if header else ""
    home = away = ""
    links = [a.get_text(" ", strip=True) for a in soup.find_all("a")]
    for index, value in enumerate(links):
        if "(主)" in value or "（主）" in value:
            home = re.sub(r"[\(（]主[\)）]", "", value).strip()
            for candidate in links[index + 1:index + 10]:
                if candidate and not any(x in candidate for x in ["Image", "分析", "指数", "胜平负", "三合一"]):
                    away = candidate.strip()
                    break
            break

    candidates = []
    for tr in soup.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if len(cells) < 13 or cells[0] in ("最大值", "最小值", "公司", ""):
            continue
        detail = tr.find("a", href=re.compile(r"/odds/3in1Odds\.aspx", re.I))
        if detail is None:
            continue
        company_match = re.search(r"companyid=(\d+)", detail.get("href", ""), re.I)
        if not company_match:
            continue
        row = BookmakerOdds(
            company=cells[0],
            ah_open_home=_f(cells[1]), ah_open_line=cells[2] or None,
            ah_open_away=_f(cells[3]),
            x12_open_home=_f(cells[7]), x12_open_draw=_f(cells[8]),
            x12_open_away=_f(cells[9]),
        )
        if (
            row.ah_open_home is not None and row.ah_open_line
            and row.ah_open_away is not None
            and row.x12_open_home is not None and row.x12_open_draw is not None
            and row.x12_open_away is not None
        ):
            rank = next(
                (i for i, key in enumerate(COMPANY_PRIORITY)
                 if key.lower() in row.company.lower()),
                len(COMPANY_PRIORITY) + len(candidates),
            )
            candidates.append((rank, company_match.group(1), row))

    rows = []
    final_score = None
    for _, company_id, row in sorted(candidates, key=lambda item: item[0]):
        try:
            asian, x12, detail_score, goals = _detail_closing(match_id, company_id)
            if final_score is None and detail_score is not None:
                final_score = detail_score
            row.ah_now_home, row.ah_now_line, row.ah_now_away = asian
            row.x12_now_home, row.x12_now_draw, row.x12_now_away = x12
            if goals is not None:
                (
                    row.ou_now_over, row.ou_now_line, row.ou_now_under,
                    row.ou_now_line_value,
                ) = goals
            rows.append(row)
        except Exception as exc:
            print(f"detail skip match={match_id} company={company_id}: {exc!r}")
        if len(rows) >= companies:
            break
    if len(rows) < companies:
        raise RuntimeError(
            f"only {len(rows)}/{companies} companies with pre-match close for {match_id}"
        )
    result = MatchOdds(
        str(match_id), league, kickoff,
        home or f"主队-{match_id}", away or f"客队-{match_id}", url, rows
    )
    result.final_score = final_score
    return result
