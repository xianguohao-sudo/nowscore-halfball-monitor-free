"""平手假强客队（PKRH / PK-Reversal Home）监控模型。

核心思想：
- Crow 初盘平手且客队低水，表面上客队更强；
- 临场仍不升到客让平/半，反而 Crow 水位翻转为主队低水；
- 欧赔同步主降客升；
- 近期状态/同主客场/对战往绩用于确认“客热”；
- 对战往绩中 Crow 的初指/终指用于识别“同初盘、不同终指”的反转。

本模块是独立模型，不修改半球假强主队、平手6分类、平/半退平模型。
"""
from __future__ import annotations

import math
import re
from statistics import median
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup

from nowscore import BASE, HEADERS, fetch_match

SCORE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[-:：]\s*(\d{1,2})(?!\d)")
MATCH_ID_PATTERNS = [
    re.compile(r"/odds/match/(\d{6,10})\.htm", re.I),
    re.compile(r"/odds/match\.aspx\?id=(\d{6,10})", re.I),
    re.compile(r"/analysis/(\d{6,10})(?:cn)?\.html", re.I),
    re.compile(r"(?:matchid|match_id|scheduleid|sid)\s*[:=]\s*[\"']?(\d{6,10})", re.I),
]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _compact(value) -> str:
    return (
        str(value or "").strip().lower()
        .replace(" ", "")
        .replace("－", "-")
        .replace("—", "-")
        .replace("／", "/")
    )


def line_value(value) -> Optional[float]:
    """统一为主队盘口：负数=主让，正数=客让。"""
    raw = _compact(value)
    if not raw:
        return None
    if raw in {"平手", "平", "0", "0.0", "0.00", "scratch", "pk"}:
        return 0.0

    receives = any(x in raw for x in ("受让", "受", "客让", "away-"))
    cleaned = raw.replace("受让", "").replace("受", "").replace("主让", "")
    cleaned = cleaned.replace("客让", "").replace("home", "").replace("away", "")
    names = [
        (("两球半/三", "2.5/3"), 2.75),
        (("两球/两球半", "2/2.5"), 2.25),
        (("球半/两", "1.5/2"), 1.75),
        (("一球/球半", "1/1.5"), 1.25),
        (("半球/一球", "半/一", "半一", "0.5/1"), 0.75),
        (("一球",), 1.0), (("球半",), 1.5), (("两球",), 2.0),
        (("平手/半球", "平/半", "平半", "0/0.5"), 0.25),
        (("半球", "半"), 0.5),
    ]
    magnitude = None
    for aliases, amount in names:
        if any(alias in cleaned for alias in aliases):
            magnitude = amount
            break
    if magnitude is None:
        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", cleaned)
        if nums:
            vals = [abs(float(x)) for x in nums]
            magnitude = sum(vals) / len(vals)
    if magnitude is None or magnitude > 4:
        return None
    return round(magnitude if receives else -magnitude, 2)


def _near(value, target, tol=0.01) -> bool:
    return value is not None and abs(value - target) <= tol


def _med(values):
    values = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return median(values) if values else None


def _crow(match):
    for row in match.rows:
        name = (row.company or "").lower()
        if "crow" in name or "皇冠" in name:
            return row
    return None


def _main_rows(match):
    keys = ("crow", "36", "易", "伟", "18", "威", "澳", "利", "盈", "明")
    selected = [r for r in match.rows if any(k in (r.company or "").lower() for k in keys)]
    return selected or list(match.rows)


def _fmt(v, digits=2):
    return "N/A" if v is None else f"{v:.{digits}f}"


def _ah_text(row):
    if not row:
        return "N/A"
    return (
        f"{_fmt(row.ah_open_home)}/{row.ah_open_line or 'N/A'}/{_fmt(row.ah_open_away)}"
        f" → {_fmt(row.ah_now_home)}/{row.ah_now_line or 'N/A'}/{_fmt(row.ah_now_away)}"
    )


def _x12_text(row):
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


def evaluate_market(match) -> Dict:
    """只看当前赔率页。先通过这一层，才抓近期/交锋，减少请求与误报。"""
    crow = _crow(match)
    rows = [r for r in _main_rows(match) if all(v is not None for v in (
        r.x12_open_home, r.x12_open_away, r.x12_now_home, r.x12_now_away
    ))]
    if crow is None:
        return {
            "qualified": False, "market_ready": False, "reliable": False,
            "score": 0, "max_score": 8, "reason": "Crow数据缺失",
        }

    ol = line_value(crow.ah_open_line)
    nl = line_value(crow.ah_now_line)
    c1 = _near(ol, 0.0)
    c2 = (
        crow.ah_open_home is not None and crow.ah_open_away is not None
        and crow.ah_open_away <= 0.92
        and crow.ah_open_home - crow.ah_open_away >= 0.04
    )
    c3 = _near(nl, 0.0)
    c4 = (
        crow.ah_now_home is not None and crow.ah_now_away is not None
        and crow.ah_now_home <= 0.90
        and crow.ah_now_away >= 0.98
        and crow.ah_now_away - crow.ah_now_home >= 0.10
    )

    home_open = _med([r.x12_open_home for r in rows])
    home_now = _med([r.x12_now_home for r in rows])
    away_open = _med([r.x12_open_away for r in rows])
    away_now = _med([r.x12_now_away for r in rows])
    home_move = None if home_open is None or home_now is None else home_now - home_open
    away_move = None if away_open is None or away_now is None else away_now - away_open
    c5 = (
        home_move is not None and away_move is not None
        and home_move <= -0.05 and away_move >= 0.05
    )

    conds = [c1, c2, c3, c4, c5]
    score = sum(conds)
    reasons = []
    if c1: reasons.append("Crow初盘=平手")
    if c2: reasons.append(f"Crow初盘客低水：{crow.ah_open_away:.2f} < 主{crow.ah_open_home:.2f}")
    if c3: reasons.append("临场仍为平手，未顺客热升到客让平/半")
    if c4: reasons.append(f"Crow水位反转：主{crow.ah_now_home:.2f}低水 / 客{crow.ah_now_away:.2f}高水")
    if c5: reasons.append(f"欧赔同步反转：主胜{home_move:+.2f} / 客胜{away_move:+.2f}")

    reliable = len(rows) >= 4
    market_ready = reliable and all(conds)
    return {
        "qualified": False,
        "market_ready": market_ready,
        "reliable": reliable,
        "score": score,
        "max_score": 8,
        "market_score": score,
        "conditions": {
            "crow_open_pk": c1,
            "crow_open_away_low": c2,
            "stay_pk_no_away_rise": c3,
            "crow_water_reverse_home": c4,
            "x12_home_down_away_up": c5,
        },
        "reasons": reasons,
        "crow_company": crow.company,
        "crow_ah": _ah_text(crow),
        "crow_x12": _x12_text(crow),
        "open_line": ol,
        "now_line": nl,
        "home_open": home_open,
        "home_now": home_now,
        "away_open": away_open,
        "away_now": away_now,
        "home_move": home_move,
        "away_move": away_move,
        "companies": len(rows),
    }


def _section_from_id_or_text(soup, element_id, marker, next_markers):
    node = soup.find(id=element_id)
    if node:
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
    score_idx, score = None, None
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
    return {
        "gf": gf, "ga": ga,
        "points": 3 if gf > ga else (1 if gf == ga else 0),
        "won": gf > ga, "draw": gf == ga, "lost": gf < ga,
        "was_home": team_idx < score_idx,
        "raw": joined,
    }


def _collect_recent(section, team, limit=10):
    records, seen = [], set()
    if not section:
        return records
    for tr in section.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if not cells:
            continue
        rec = _row_team_result(cells, team)
        if not rec or rec["raw"] in seen:
            continue
        seen.add(rec["raw"])
        records.append(rec)
        if len(records) >= limit:
            break
    return records


def _summary(records):
    n = len(records)
    if not n:
        return {"available": False, "matches": 0, "summary": "N/A"}
    pts = sum(r["points"] for r in records)
    gf = sum(r["gf"] for r in records)
    ga = sum(r["ga"] for r in records)
    w = sum(r["won"] for r in records)
    d = sum(r["draw"] for r in records)
    l = n - w - d
    return {
        "available": True, "matches": n,
        "wins": w, "draws": d, "losses": l,
        "gf": gf, "ga": ga,
        "ppg": pts / n,
        "gdpg": (gf - ga) / n,
        "loss_rate": l / n,
        "summary": f"近{n}场 {w}胜{d}平{l}负，PPG {pts/n:.2f}，净胜/场 {(gf-ga)/n:+.2f}",
    }


def _extract_match_id(raw_html: str) -> Optional[str]:
    for pat in MATCH_ID_PATTERNS:
        m = pat.search(raw_html or "")
        if m:
            return m.group(1)
    return None


def _collect_h2h(section, home_team, away_team, limit=10):
    records, seen = [], set()
    if not section:
        return records
    for tr in section.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        joined = " ".join(cells)
        if home_team not in joined or away_team not in joined or joined in seen:
            continue
        rh = _row_team_result(cells, home_team)
        ra = _row_team_result(cells, away_team)
        if not rh or not ra:
            continue
        seen.add(joined)
        records.append({
            "home_result": rh,
            "away_result": ra,
            "match_id": _extract_match_id(str(tr)),
            "raw": joined,
        })
        if len(records) >= limit:
            break
    return records


def _h2h_summary(records):
    n = len(records)
    if not n:
        return {"available": False, "matches": 0, "summary": "N/A"}
    hw = sum(r["home_result"]["won"] for r in records)
    aw = sum(r["away_result"]["won"] for r in records)
    dr = n - hw - aw
    same_role = [r for r in records if r["home_result"]["was_home"]]
    last_same = same_role[0] if same_role else None
    return {
        "available": True,
        "matches": n,
        "home_wins": hw, "draws": dr, "away_wins": aw,
        "away_win_rate": aw / n,
        "same_role_matches": len(same_role),
        "last_same_role": last_same,
        "summary": f"近{n}次：当前主队{hw}胜{dr}平{aw}负；同主客场样本{len(same_role)}场",
    }


def _historical_crow_confirmation(current_match, h2h: Dict) -> Dict:
    """只抓最近1场同主客场交锋，比较 Crow 初指/终指，避免请求过多。"""
    result = {
        "available": False, "confirmed": False, "match_id": None,
        "summary": "对战往绩Crow：N/A",
    }
    rec = h2h.get("last_same_role") or {}
    hid = rec.get("match_id")
    if not hid or str(hid) == str(current_match.match_id):
        return result
    try:
        old = fetch_match(str(hid))
        row = _crow(old)
        if not row:
            result["summary"] = f"对战往绩Crow：历史比赛{hid}无Crow数据"
            return result
        ol = line_value(row.ah_open_line)
        nl = line_value(row.ah_now_line)
        open_away_low = (
            _near(ol, 0.0)
            and row.ah_open_home is not None and row.ah_open_away is not None
            and row.ah_open_away <= row.ah_open_home - 0.03
        )
        close_still_away = (
            (_near(nl, 0.0) and row.ah_now_home is not None and row.ah_now_away is not None
             and row.ah_now_away <= row.ah_now_home - 0.03)
            or (nl is not None and nl >= 0.25)
        )
        prior_away_win = bool(rec.get("away_result", {}).get("won"))
        confirmed = open_away_low and close_still_away and prior_away_win
        result.update({
            "available": True,
            "confirmed": confirmed,
            "match_id": str(hid),
            "prior_away_win": prior_away_win,
            "open_away_low": open_away_low,
            "close_still_away": close_still_away,
            "ah": _ah_text(row),
            "x12": _x12_text(row),
            "summary": (
                f"最近同主客场{old.home} vs {old.away}：Crow {_ah_text(row)}；"
                f"结果={'客胜' if prior_away_win else '非客胜'}；"
                f"{'形成“同初盘、旧终指偏客/本场终指反主”确认' if confirmed else '未形成强确认'}"
            ),
        })
        return result
    except Exception as exc:
        result["error"] = repr(exc)
        result["summary"] = f"对战往绩Crow抓取失败：{exc!r}"
        return result


def fetch_context(match) -> Dict:
    """抓近期战绩、同主/同客走势和对战往绩；失败时不阻断主流程。"""
    url = f"{BASE}/analysis/{match.match_id}cn.html"
    result = {
        "available": False, "url": url,
        "home_recent": {"available": False, "matches": 0, "summary": "N/A"},
        "away_recent": {"available": False, "matches": 0, "summary": "N/A"},
        "home_venue": {"available": False, "matches": 0, "summary": "N/A"},
        "away_venue": {"available": False, "matches": 0, "summary": "N/A"},
        "h2h": {"available": False, "matches": 0, "summary": "N/A"},
        "h2h_crow": {"available": False, "confirmed": False, "summary": "对战往绩Crow：N/A"},
    }
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "lxml")
        h2h_section = _section_from_id_or_text(soup, "porlet_3", "对战往绩", ["近期战绩", "盘路比较", "联赛积分"])
        recent_section = _section_from_id_or_text(soup, "porlet_5", "近期战绩", ["未来赛事", "盘路比较", "联赛积分"])
        if not recent_section or len(_clean(recent_section.get_text(" ", strip=True))) < 50:
            recent_section = _section_from_id_or_text(soup, "porlet_6", "近期战绩", ["未来赛事", "盘路比较", "联赛积分"])

        home_records = _collect_recent(recent_section, match.home, 10)
        away_records = _collect_recent(recent_section, match.away, 10)
        home_recent = _summary(home_records)
        away_recent = _summary(away_records)
        home_venue = _summary([r for r in home_records if r["was_home"]])
        away_venue = _summary([r for r in away_records if not r["was_home"]])
        h2h_records = _collect_h2h(h2h_section, match.home, match.away, 10)
        h2h = _h2h_summary(h2h_records)
        h2h_crow = _historical_crow_confirmation(match, h2h)

        result.update({
            "available": any(x.get("available") for x in (home_recent, away_recent, h2h)),
            "home_recent": home_recent,
            "away_recent": away_recent,
            "home_venue": home_venue,
            "away_venue": away_venue,
            "h2h": h2h,
            "h2h_crow": h2h_crow,
        })
        return result
    except Exception as exc:
        result["error"] = repr(exc)
        return result


def enrich_with_context(market: Dict, context: Dict) -> Dict:
    result = dict(market)
    result["context"] = context
    conds = dict(result.get("conditions") or {})

    hr = context.get("home_recent") or {}
    ar = context.get("away_recent") or {}
    hv = context.get("home_venue") or {}
    av = context.get("away_venue") or {}
    h2h = context.get("h2h") or {}
    hc = context.get("h2h_crow") or {}

    c6 = bool(
        hr.get("available") and ar.get("available") and (
            ar.get("ppg", 0) - hr.get("ppg", 0) >= 0.35
            or ar.get("gdpg", 0) - hr.get("gdpg", 0) >= 0.50
        )
    )
    c7 = bool(
        hv.get("available") and av.get("available")
        and hv.get("matches", 0) >= 2 and av.get("matches", 0) >= 2
        and (
            hv.get("ppg", 0) - av.get("ppg", 0) >= 0.35
            or (hv.get("loss_rate", 1) <= 0.25 and av.get("loss_rate", 0) >= 0.50)
        )
    )
    last_same = h2h.get("last_same_role") or {}
    h2h_away_hot = bool(
        (h2h.get("available") and h2h.get("matches", 0) >= 3
         and h2h.get("away_wins", 0) > h2h.get("home_wins", 0))
        or last_same.get("away_result", {}).get("won")
    )
    c8 = bool(hc.get("confirmed"))

    conds.update({
        "away_recent_hot": c6,
        "venue_split_support_home": c7,
        "h2h_away_hot": h2h_away_hot,
        "h2h_crow_reverse_confirm": c8,
    })

    score = int(sum(bool(conds.get(k)) for k in (
        "crow_open_pk", "crow_open_away_low", "stay_pk_no_away_rise",
        "crow_water_reverse_home", "x12_home_down_away_up",
        "away_recent_hot", "venue_split_support_home", "h2h_crow_reverse_confirm",
    )))

    reasons = list(result.get("reasons") or [])
    if c6:
        reasons.append(f"客队近期更热：客PPG {ar.get('ppg',0):.2f} vs 主{hr.get('ppg',0):.2f}")
    if c7:
        reasons.append(f"主客场拆分反向支持主队：主队主场PPG {hv.get('ppg',0):.2f} vs 客队客场{av.get('ppg',0):.2f}")
    if h2h_away_hot:
        reasons.append(f"交锋层存在客热背景：{h2h.get('summary','N/A')}")
    if c8:
        reasons.append(hc.get("summary", "H2H Crow终指反转确认"))

    qualified = bool(result.get("market_ready")) and score >= 6
    if qualified and score >= 8:
        grade, risk, lean = "S+", "较低-中", "主胜优先；防平"
    elif qualified and score == 7:
        grade, risk, lean = "S", "中等偏低", "主胜优先；平局保护"
    elif qualified:
        grade, risk, lean = "A", "中等", "主不败，主胜优先"
    elif result.get("market_ready"):
        grade, risk, lean = "B", "中等偏高", "盘口成立但确认不足，观察"
    else:
        grade, risk, lean = "C", "高", "不触发"

    result.update({
        "qualified": qualified,
        "score": score,
        "max_score": 8,
        "conditions": conds,
        "reasons": reasons,
        "h2h_away_hot": h2h_away_hot,
        "grade": grade,
        "risk": risk,
        "lean": lean,
    })
    return result
