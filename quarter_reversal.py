"""客让平/半退平手 -> 主胜反转监控模型 V2。

判断分三层：
A. 盘口核心（硬门槛，6项）
   1) 主流初盘为 +0.25（即客队让平/半）
   2) 即时/临场主流退到 0（平手）
   3) 欧赔主胜中位明显下降
   4) 欧赔客胜中位明显上升
   5) 客队初始为1X2热门方
   6) 平赔没有同步大幅压低
B. 基本面/对战确认（只加强，绝不救活核心不合格比赛）
   - 客队近期积分效率明显高于主队
   - 客队近期净胜球效率明显高于主队
   - 历史交锋客队总体占优（形成客热）
   - 当前主队在同主客场交锋中存在抵抗力
C. 历史同盘确认
   - NowScore“相同历史指数”中客队同角色盘路偏差

新增 BTI（Basic-form vs Trading divergence Index，0-10）：
盘口反转强度 0-6 + 客队基本面热度 0-3 + 主队历史抵抗 0-1。
BTI 越高，表示“基本面越热客，但盘口越撤客”的背离越强。

重要：近期战绩/对战往绩只用于确认和升级，不改变 qualified 的硬门槛。
"""
import re
from statistics import median

import requests
from bs4 import BeautifulSoup

from quarter_classifier import line_value
from nowscore import BASE, HEADERS

PREFERRED = ["36", "Crow", "易", "伟", "18", "威", "澳", "利", "盈", "明"]
SCORE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[-:：]\s*(\d{1,2})(?!\d)")


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
    """当前欧亚盘核心判断；这一层不请求分析页。"""
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
            "qualified": False, "reliable": False,
            "core_score": 0, "max_core_score": 6,
            "reason": "insufficient_companies", "companies": len(usable),
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

    c1 = len(open_away_quarter) >= 3 and open_q_ratio >= 0.60
    c2 = len(q_to_flat) >= 3 and flat_ratio >= 0.60 and transition_ratio >= 0.60
    c3 = home_move is not None and home_move <= -0.15
    c4 = away_move is not None and away_move >= 0.15
    c5 = (
        away_open is not None and home_open is not None
        and away_open < home_open and away_open <= 2.35
    )
    c6 = draw_move is not None and draw_move >= -0.10

    conds = [c1, c2, c3, c4, c5, c6]
    core_score = sum(conds)
    reliable = c1 and c2 and len(usable) >= 4
    # 硬条件：退盘共识 + 主胜降 + 客胜升 + 总分>=5。
    qualified = reliable and c3 and c4 and core_score >= 5

    reasons = []
    if c1: reasons.append(f"主流初盘客让平/半（{len(open_away_quarter)}/{len(usable)}家）")
    if c2: reasons.append(f"客让平/半→平手形成共识（转盘{len(q_to_flat)}家；当前平手{len(flat_now)}/{len(usable)}家）")
    if c3: reasons.append(f"主胜中位赔率明显下压（{home_move:+.2f}）")
    if c4: reasons.append(f"客胜中位赔率明显抬升（{away_move:+.2f}）")
    if c5: reasons.append(f"客队初始为欧赔热门（客{away_open:.2f} < 主{home_open:.2f}）")
    if c6: reasons.append(f"平赔未明显压低（变化{draw_move:+.2f}）")

    rep_pool = [item[0] for item in q_to_flat] or [item[0] for item in usable]
    rep = next((row for key in ["Crow", "36", "18", "易", "伟"] for row in rep_pool if key.lower() in row.company.lower()), rep_pool[0] if rep_pool else None)

    def x12(row):
        if not row: return "N/A"
        vals = [row.x12_open_home, row.x12_open_draw, row.x12_open_away, row.x12_now_home, row.x12_now_draw, row.x12_now_away]
        if any(v is None for v in vals): return "N/A"
        return f"{row.x12_open_home:.2f}/{row.x12_open_draw:.2f}/{row.x12_open_away:.2f} → {row.x12_now_home:.2f}/{row.x12_now_draw:.2f}/{row.x12_now_away:.2f}"

    def ah(row):
        if not row or not row.ah_open_line or not row.ah_now_line: return "N/A"
        return f"{_fmt(row.ah_open_home)}/{row.ah_open_line}/{_fmt(row.ah_open_away)} → {_fmt(row.ah_now_home)}/{row.ah_now_line}/{_fmt(row.ah_now_away)}"

    if qualified and core_score == 6 and home_move <= -0.25 and away_move >= 0.20:
        grade, risk, lean = "S", "低-中", "主胜优先；防平"
    elif qualified:
        grade, risk, lean = "A", "中等", "主不败，主胜优先"
    elif core_score >= 4:
        grade, risk, lean = "B", "中等偏高", "观察主队方向，不单独触发"
    else:
        grade, risk, lean = "C", "高", "不触发"

    return {
        "qualified": qualified, "reliable": reliable,
        "core_score": core_score, "max_core_score": 6,
        "grade": grade, "risk": risk, "lean": lean, "reasons": reasons,
        "companies": len(usable), "open_quarter_count": len(open_away_quarter),
        "flat_now_count": len(flat_now), "transition_count": len(q_to_flat),
        "open_quarter_ratio": open_q_ratio, "flat_ratio": flat_ratio, "transition_ratio": transition_ratio,
        "home_open": home_open, "home_now": home_now,
        "draw_open": draw_open, "draw_now": draw_now,
        "away_open": away_open, "away_now": away_now,
        "home_move": home_move, "draw_move": draw_move, "away_move": away_move,
        "rep": rep.company if rep else "N/A", "x12": x12(rep), "ah": ah(rep),
    }


def _section_from_id_or_text(soup, element_id, marker, next_markers):
    node = soup.find(id=element_id)
    if node:
        # NowScore 的 porlet 往往整个模块就在父级容器中。
        parent = node
        for _ in range(3):
            if parent and len(_clean(parent.get_text(" ", strip=True))) < 500:
                parent = parent.parent
        if parent:
            return BeautifulSoup(str(parent), "lxml")
    html = str(soup)
    start = html.find(marker)
    if start < 0:
        return None
    ends = [html.find(x, start + len(marker)) for x in next_markers]
    ends = [x for x in ends if x > start]
    end = min(ends) if ends else min(len(html), start + 180000)
    return BeautifulSoup(html[start:end], "lxml")


def _row_team_result(cells, team):
    joined = " ".join(cells)
    if team not in joined:
        return None
    score_idx = None
    score = None
    for i, cell in enumerate(cells):
        m = SCORE_RE.search(cell)
        if m:
            score_idx, score = i, (int(m.group(1)), int(m.group(2)))
            break
    if score_idx is None:
        return None
    team_indices = [i for i, cell in enumerate(cells) if team in cell]
    if not team_indices:
        return None
    team_idx = min(team_indices, key=lambda x: abs(x - score_idx))
    gf, ga = score if team_idx < score_idx else (score[1], score[0])
    points = 3 if gf > ga else (1 if gf == ga else 0)
    return {"gf": gf, "ga": ga, "points": points, "won": gf > ga, "draw": gf == ga, "lost": gf < ga, "was_home": team_idx < score_idx, "raw": joined}


def _collect_recent(section, team, limit=10):
    records, seen = [], set()
    if not section:
        return records
    for tr in section.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if not cells: continue
        rec = _row_team_result(cells, team)
        if not rec: continue
        key = rec["raw"]
        if key in seen: continue
        seen.add(key); records.append(rec)
        if len(records) >= limit: break
    return records


def _summarize_recent(records):
    n = len(records)
    if not n:
        return {"available": False, "matches": 0}
    pts = sum(r["points"] for r in records)
    gf = sum(r["gf"] for r in records); ga = sum(r["ga"] for r in records)
    wins = sum(r["won"] for r in records); draws = sum(r["draw"] for r in records); losses = sum(r["lost"] for r in records)
    return {
        "available": True, "matches": n, "wins": wins, "draws": draws, "losses": losses,
        "gf": gf, "ga": ga, "ppg": pts / n, "gdpg": (gf - ga) / n,
        "summary": f"近{n}场 {wins}胜{draws}平{losses}负，场均积分{pts/n:.2f}，场均净胜{(gf-ga)/n:+.2f}",
    }


def _collect_h2h(section, home_team, away_team, limit=10):
    records, seen = [], set()
    if not section:
        return records
    for tr in section.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        joined = " ".join(cells)
        if home_team not in joined or away_team not in joined: continue
        rh = _row_team_result(cells, home_team)
        ra = _row_team_result(cells, away_team)
        if not rh or not ra: continue
        if joined in seen: continue
        seen.add(joined)
        records.append({"home_result": rh, "away_result": ra, "raw": joined})
        if len(records) >= limit: break
    return records


def _summarize_h2h(records):
    n = len(records)
    if not n:
        return {"available": False, "matches": 0}
    hw = sum(r["home_result"]["won"] for r in records)
    aw = sum(r["away_result"]["won"] for r in records)
    dr = n - hw - aw
    current_home_at_home = [r for r in records if r["home_result"]["was_home"]]
    home_home_wins = sum(r["home_result"]["won"] for r in current_home_at_home[:5])
    return {
        "available": True, "matches": n, "home_wins": hw, "draws": dr, "away_wins": aw,
        "away_win_rate": aw / n, "current_home_home_samples": len(current_home_at_home[:5]),
        "current_home_home_wins": home_home_wins,
        "summary": f"近{n}次交锋：当前主队{hw}胜{dr}平{aw}负；同主客场主队近{len(current_home_at_home[:5])}次赢{home_home_wins}次",
    }


def fetch_form_h2h_context(match):
    """抓对战往绩(#porlet_3)和近期战绩(#porlet_6)。失败时 fail-open。"""
    url = f"{BASE}/analysis/{match.match_id}cn.html"
    result = {
        "available": False, "url": url,
        "home_recent": {"available": False, "matches": 0},
        "away_recent": {"available": False, "matches": 0},
        "h2h": {"available": False, "matches": 0},
    }
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status(); response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "lxml")
        h2h_section = _section_from_id_or_text(soup, "porlet_3", "对战往绩", ["近期战绩", "盘路比较", "联赛积分"])
        recent_section = _section_from_id_or_text(soup, "porlet_6", "近期战绩", ["未来赛事", "盘路比较", "联赛积分"])
        home_recent = _summarize_recent(_collect_recent(recent_section, match.home, 10))
        away_recent = _summarize_recent(_collect_recent(recent_section, match.away, 10))
        h2h = _summarize_h2h(_collect_h2h(h2h_section, match.home, match.away, 10))
        result.update({"available": home_recent.get("available") or away_recent.get("available") or h2h.get("available"), "home_recent": home_recent, "away_recent": away_recent, "h2h": h2h})
        return result
    except Exception as exc:
        result["error"] = repr(exc); return result


def fetch_same_handicap_history(match):
    """抓“相同历史指数”，只作为加强项。"""
    url = f"{BASE}/analysis/{match.match_id}cn.html"
    result = {"available": False, "url": url, "total": 0, "wins": 0, "draws": 0, "losses": 0, "cover_rate": None, "poor": False, "summary": "历史同盘：N/A"}
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status(); response.encoding = response.apparent_encoding or "utf-8"
        html = response.text
        start = html.find("相同历史指数")
        if start < 0: return result
        end = html.find("近期相似指数", start)
        if end < 0: end = min(len(html), start + 120000)
        segment = BeautifulSoup(html[start:end], "lxml")
        records = set()
        for table in segment.find_all("table"):
            table_text = _clean(table.get_text(" ", strip=True))
            if match.away not in table_text or "盘路" not in table_text: continue
            for tr in table.find_all("tr"):
                cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
                if len(cells) < 5: continue
                joined = " ".join(cells)
                if "赛事" in joined and "盘路" in joined: continue
                wl = next((cell.upper() for cell in reversed(cells) if cell.upper() in {"W", "D", "L"}), None)
                if not wl: continue
                if not any("平/半" in cell or "平半" in cell or cell in {"0.25", "-0.25", "+0.25"} for cell in cells): continue
                if match.away not in joined: continue
                records.add(tuple(cells))
        outcomes = [next((cell.upper() for cell in reversed(cells) if cell.upper() in {"W", "D", "L"}), None) for cells in records]
        outcomes = [x for x in outcomes if x]
        if not outcomes: return result
        wins, draws, losses = outcomes.count("W"), outcomes.count("D"), outcomes.count("L")
        total = len(outcomes); cover_rate = wins / total
        poor = total >= 2 and ((wins == 0 and losses >= 2) or (cover_rate <= 0.34 and losses > wins))
        result.update({"available": True, "total": total, "wins": wins, "draws": draws, "losses": losses, "cover_rate": cover_rate, "poor": poor, "summary": f"客队相同历史指数盘路：{wins}赢{draws}走{losses}输（赢盘率{cover_rate*100:.0f}%）"})
        return result
    except Exception as exc:
        result["error"] = repr(exc); return result


def enrich_with_context(core_result, history, context):
    """V2确认层：历史同盘 + 近期战绩 + 对战往绩 + BTI。"""
    result = dict(core_result)
    result["history"] = history
    result["context"] = context

    hp = int(bool(history.get("poor")))
    home_recent = context.get("home_recent") or {}
    away_recent = context.get("away_recent") or {}
    h2h = context.get("h2h") or {}

    recent_ppg_hot = bool(home_recent.get("available") and away_recent.get("available") and away_recent.get("ppg", 0) - home_recent.get("ppg", 0) >= 0.50)
    recent_gd_hot = bool(home_recent.get("available") and away_recent.get("available") and away_recent.get("gdpg", 0) - home_recent.get("gdpg", 0) >= 0.50)
    h2h_away_edge = bool(h2h.get("available") and h2h.get("matches", 0) >= 3 and h2h.get("away_win_rate", 0) >= 0.50 and h2h.get("away_wins", 0) > h2h.get("home_wins", 0))
    home_resistance = bool(h2h.get("available") and h2h.get("current_home_home_samples", 0) >= 1 and h2h.get("current_home_home_wins", 0) >= 1)

    heat_points = int(recent_ppg_hot) + int(recent_gd_hot) + int(h2h_away_edge)
    resistance_point = int(home_resistance)
    confirmation_points = hp + heat_points + resistance_point

    # BTI: 0-10。盘口反转占6，客热占3，主场历史抵抗占1。
    bti = min(10, int(result.get("core_score", 0)) + heat_points + resistance_point)

    result.update({
        "history_point": hp,
        "recent_ppg_hot": recent_ppg_hot,
        "recent_gd_hot": recent_gd_hot,
        "h2h_away_edge": h2h_away_edge,
        "home_resistance": home_resistance,
        "heat_points": heat_points,
        "resistance_point": resistance_point,
        "confirmation_points": confirmation_points,
        "bti": bti,
        "score": result.get("core_score", 0) + confirmation_points,
        "max_score": 11,
    })

    extra = []
    if recent_ppg_hot: extra.append(f"客队近期积分效率明显更强：{away_recent.get('ppg',0):.2f} vs {home_recent.get('ppg',0):.2f}")
    if recent_gd_hot: extra.append(f"客队近期净胜球效率明显更强：{away_recent.get('gdpg',0):+.2f} vs {home_recent.get('gdpg',0):+.2f}")
    if h2h_away_edge: extra.append(f"历史交锋客队占优：{h2h.get('summary','')}")
    if home_resistance: extra.append(f"但当前主队同主客场存在历史抵抗：主场近{h2h.get('current_home_home_samples',0)}次赢{h2h.get('current_home_home_wins',0)}次")
    if hp: extra.append(history.get("summary", "客队同盘历史偏差"))
    result["reasons"] = list(result.get("reasons", [])) + extra

    # 硬门槛不变；确认层只升级，不降级/救活。
    if not result.get("qualified"):
        return result
    if bti >= 9 and hp:
        result["grade"], result["risk"], result["lean"] = "S+", "较低", "主胜优先；平局作保护"
    elif bti >= 8:
        result["grade"], result["risk"], result["lean"] = "S", "低-中", "主胜优先；防平"
    elif confirmation_points >= 2:
        result["grade"], result["risk"], result["lean"] = "A+", "中等偏低", "主胜优先；平局保护"
    return result


def enrich_with_history(core_result, history):
    """兼容旧调用；没有context时只做历史同盘确认。"""
    return enrich_with_context(core_result, history, {"home_recent": {}, "away_recent": {}, "h2h": {}})
