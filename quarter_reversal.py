"""客让平/半退平手 -> 主胜反转监控模型。

核心结构（主队视角）：
1) 主流初盘为 +0.25（即客队让平/半）
2) 临场/即时主流退到 0（平手）
3) 欧赔主胜明显下降
4) 欧赔客胜明显上升
5) 客队初始为1X2热门方
6) 平赔没有同步大幅压低
7) NowScore“相同历史指数”中，客队同角色盘路偏差（可选加强项）

历史项仅用于加分，不作为触发硬条件；分析页抓取失败时不阻断监控。
"""
import re
from statistics import median

import requests
from bs4 import BeautifulSoup

from quarter_classifier import line_value
from nowscore import BASE, HEADERS

PREFERRED = ["36", "Crow", "易", "伟", "18", "威", "澳", "利", "盈", "明"]


def _clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _median(values):
    values = [v for v in values if v is not None]
    return median(values) if values else None


def _selected_rows(match):
    rows = [
        row for row in match.rows
        if any(key.lower() in row.company.lower() for key in PREFERRED)
    ]
    return rows or list(match.rows)


def _near(value, target, tol=0.01):
    return value is not None and abs(value - target) <= tol


def _fmt(value, digits=2):
    return "N/A" if value is None else f"{value:.{digits}f}"


def evaluate_quarter_reversal(match):
    """只做当前欧亚盘核心判断；不请求分析页。"""
    usable = []
    for row in _selected_rows(match):
        open_line = line_value(row.ah_open_line)
        now_line = line_value(row.ah_now_line)
        if (
            open_line is None or now_line is None
            or row.x12_open_home is None or row.x12_open_draw is None or row.x12_open_away is None
            or row.x12_now_home is None or row.x12_now_draw is None or row.x12_now_away is None
        ):
            continue
        usable.append((row, open_line, now_line))

    if len(usable) < 4:
        return {
            "qualified": False,
            "reliable": False,
            "core_score": 0,
            "max_core_score": 6,
            "reason": "insufficient_companies",
            "companies": len(usable),
        }

    open_away_quarter = [(row, ol, nl) for row, ol, nl in usable if _near(ol, 0.25)]
    flat_now = [(row, ol, nl) for row, ol, nl in usable if _near(nl, 0.0)]
    q_to_flat = [(row, ol, nl) for row, ol, nl in usable if _near(ol, 0.25) and _near(nl, 0.0)]

    open_q_ratio = len(open_away_quarter) / len(usable)
    flat_ratio = len(flat_now) / len(usable)
    transition_ratio = len(q_to_flat) / len(open_away_quarter) if open_away_quarter else 0.0

    home_open = _median([row.x12_open_home for row, _, _ in usable])
    home_now = _median([row.x12_now_home for row, _, _ in usable])
    draw_open = _median([row.x12_open_draw for row, _, _ in usable])
    draw_now = _median([row.x12_now_draw for row, _, _ in usable])
    away_open = _median([row.x12_open_away for row, _, _ in usable])
    away_now = _median([row.x12_now_away for row, _, _ in usable])

    home_move = None if home_open is None or home_now is None else home_now - home_open
    draw_move = None if draw_open is None or draw_now is None else draw_now - draw_open
    away_move = None if away_open is None or away_now is None else away_now - away_open

    # 6项核心指纹。
    c1 = len(open_away_quarter) >= 3 and open_q_ratio >= 0.60
    c2 = len(q_to_flat) >= 3 and flat_ratio >= 0.60 and transition_ratio >= 0.60
    c3 = home_move is not None and home_move <= -0.15
    c4 = away_move is not None and away_move >= 0.15
    c5 = (
        away_open is not None and home_open is not None
        and away_open < home_open
        and away_open <= 2.35
    )
    # 平赔若也大压，可能只是“防平”而不是胜负权重直接转向主队。
    c6 = draw_move is not None and draw_move >= -0.10

    conds = [c1, c2, c3, c4, c5, c6]
    core_score = sum(conds)
    reliable = c1 and c2 and len(usable) >= 4

    # 主胜下降 + 客胜抬升是本模型的硬条件，避免只凭退盘误报。
    qualified = reliable and c3 and c4 and core_score >= 5

    reasons = []
    if c1:
        reasons.append(f"主流初盘客让平/半（{len(open_away_quarter)}/{len(usable)}家）")
    if c2:
        reasons.append(
            f"客让平/半→平手形成共识（转盘{len(q_to_flat)}家；当前平手{len(flat_now)}/{len(usable)}家）"
        )
    if c3:
        reasons.append(f"主胜中位赔率明显下压（{home_move:+.2f}）")
    if c4:
        reasons.append(f"客胜中位赔率明显抬升（{away_move:+.2f}）")
    if c5:
        reasons.append(f"客队初始为欧赔热门（客{away_open:.2f} < 主{home_open:.2f}）")
    if c6:
        reasons.append(f"平赔未明显压低（变化{draw_move:+.2f}）")

    rep_pool = [item[0] for item in q_to_flat] or [item[0] for item in usable]
    rep = next(
        (
            row for key in ["Crow", "36", "18", "易", "伟"]
            for row in rep_pool if key.lower() in row.company.lower()
        ),
        rep_pool[0] if rep_pool else None,
    )

    def x12(row):
        if not row:
            return "N/A"
        vals = [
            row.x12_open_home, row.x12_open_draw, row.x12_open_away,
            row.x12_now_home, row.x12_now_draw, row.x12_now_away,
        ]
        if any(v is None for v in vals):
            return "N/A"
        return (
            f"{row.x12_open_home:.2f}/{row.x12_open_draw:.2f}/{row.x12_open_away:.2f}"
            f" → {row.x12_now_home:.2f}/{row.x12_now_draw:.2f}/{row.x12_now_away:.2f}"
        )

    def ah(row):
        if not row or not row.ah_open_line or not row.ah_now_line:
            return "N/A"
        return (
            f"{_fmt(row.ah_open_home)}/{row.ah_open_line}/{_fmt(row.ah_open_away)}"
            f" → {_fmt(row.ah_now_home)}/{row.ah_now_line}/{_fmt(row.ah_now_away)}"
        )

    if qualified and core_score == 6 and home_move <= -0.25 and away_move >= 0.20:
        grade = "S"
        risk = "低-中"
        lean = "主胜优先；防平"
    elif qualified and core_score >= 5:
        grade = "A"
        risk = "中等"
        lean = "主不败，主胜优先"
    elif core_score >= 4:
        grade = "B"
        risk = "中等偏高"
        lean = "观察主队方向，不单独触发"
    else:
        grade = "C"
        risk = "高"
        lean = "不触发"

    return {
        "qualified": qualified,
        "reliable": reliable,
        "core_score": core_score,
        "max_core_score": 6,
        "grade": grade,
        "risk": risk,
        "lean": lean,
        "reasons": reasons,
        "companies": len(usable),
        "open_quarter_count": len(open_away_quarter),
        "flat_now_count": len(flat_now),
        "transition_count": len(q_to_flat),
        "open_quarter_ratio": open_q_ratio,
        "flat_ratio": flat_ratio,
        "transition_ratio": transition_ratio,
        "home_open": home_open,
        "home_now": home_now,
        "draw_open": draw_open,
        "draw_now": draw_now,
        "away_open": away_open,
        "away_now": away_now,
        "home_move": home_move,
        "draw_move": draw_move,
        "away_move": away_move,
        "rep": rep.company if rep else "N/A",
        "x12": x12(rep),
        "ah": ah(rep),
    }


def fetch_same_handicap_history(match):
    """抓取分析页“相同历史指数”，只作为客队历史盘路加强项。

    返回 away 表中相同指数的 W/D/L。页面结构变动或访问失败时 fail-open。
    """
    url = f"{BASE}/analysis/{match.match_id}cn.html"
    result = {
        "available": False,
        "url": url,
        "total": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "cover_rate": None,
        "poor": False,
        "summary": "历史同盘：N/A",
    }
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        html = response.text

        start = html.find("相同历史指数")
        if start < 0:
            return result
        end = html.find("近期相似指数", start)
        if end < 0:
            end = min(len(html), start + 120000)

        # 截出这一段后再解析，避免误把页面其它表的W/L算进来。
        segment = BeautifulSoup(html[start:end], "lxml")
        records = set()

        for table in segment.find_all("table"):
            table_text = _clean(table.get_text(" ", strip=True))
            if match.away not in table_text or "盘路" not in table_text:
                continue

            for tr in table.find_all("tr"):
                cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
                if len(cells) < 5:
                    continue
                joined = " ".join(cells)
                if "赛事" in joined and "盘路" in joined:
                    continue

                wl = next((cell.upper() for cell in reversed(cells) if cell.upper() in {"W", "D", "L"}), None)
                if not wl:
                    continue
                # “相同历史指数”理论上已经是同指；再要求行里出现平/半，防止嵌套表污染。
                if not any("平/半" in cell or "平半" in cell or cell in {"0.25", "-0.25", "+0.25"} for cell in cells):
                    continue
                # 当前客队必须出现在这一行。
                if match.away not in joined:
                    continue

                records.add(tuple(cells))

        outcomes = []
        for cells in records:
            wl = next((cell.upper() for cell in reversed(cells) if cell.upper() in {"W", "D", "L"}), None)
            if wl:
                outcomes.append(wl)

        if not outcomes:
            return result

        wins = outcomes.count("W")
        draws = outcomes.count("D")
        losses = outcomes.count("L")
        total = len(outcomes)
        cover_rate = wins / total if total else None

        # 至少2场才把历史项当成有效加强证据。
        poor = total >= 2 and (
            (wins == 0 and losses >= 2)
            or (cover_rate is not None and cover_rate <= 0.34 and losses > wins)
        )

        result.update({
            "available": True,
            "total": total,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "cover_rate": cover_rate,
            "poor": poor,
            "summary": (
                f"客队相同历史指数盘路：{wins}赢{draws}走{losses}输"
                f"（赢盘率{cover_rate * 100:.0f}%）"
            ),
        })
        return result
    except Exception as exc:
        result["error"] = repr(exc)
        return result


def enrich_with_history(core_result, history):
    """将可选历史证据加入7项总评分，并给出最终级别。"""
    result = dict(core_result)
    history_point = int(bool(history.get("poor")))
    result["history"] = history
    result["history_point"] = history_point
    result["score"] = result.get("core_score", 0) + history_point
    result["max_score"] = 7

    if not result.get("qualified"):
        return result

    if history_point and result["core_score"] == 6:
        result["grade"] = "S+"
        result["risk"] = "较低"
        result["lean"] = "主胜优先；平局作保护"
        result["reasons"] = list(result.get("reasons", [])) + [history["summary"]]
    elif history_point:
        result["grade"] = "S"
        result["risk"] = "低-中"
        result["lean"] = "主胜优先；防平"
        result["reasons"] = list(result.get("reasons", [])) + [history["summary"]]

    return result
