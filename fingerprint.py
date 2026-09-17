from statistics import median

PREFERRED = ["36","Crow","易","伟","18","威","澳","利","盈","明"]


def _norm_line(s):
    return (s or "").replace(" ", "").replace("主", "")


def _half(s):
    return _norm_line(s) in {"半球", "0.5", "-0.5", "半"}


def _quarter(s):
    return _norm_line(s) in {"平/半", "平半", "0.25", "-0.25"}


def _threeq(s):
    return _norm_line(s) in {"半/一", "半一", "0.75", "-0.75"}


def _three_layer_shadow(m, market_score, market_candidate):
    """三层判断的第2/3层（影子模式）。

    Layer 1 = 原盘口模型，完全不改阈值。
    Layer 2 = 近期战绩：双方近10场 PPG / 场均净胜球。
    Layer 3 = 对战往绩：总体交锋 + 当前主队同主客场抵抗。

    目前只做风险标记和数据积累，不否决、不救活、不改变原模型score/reliable。
    这样可以先积累50/100场，再验证是否真的提高命中率。
    """
    base = {
        "mode": "shadow",
        "available": False,
        "level": "N/A",
        "home_support_points": 0,
        "away_support_points": 0,
        "summary": "三层影子：N/A",
        "home_recent": {},
        "away_recent": {},
        "h2h": {},
    }
    if not market_candidate or market_score < 4:
        return base
    try:
        # 复用已验证的分析页解析器；失败时fail-open，不影响原半球模型。
        from quarter_reversal import fetch_form_h2h_context
        ctx = fetch_form_h2h_context(m)
        hr = ctx.get("home_recent") or {}
        ar = ctx.get("away_recent") or {}
        h2h = ctx.get("h2h") or {}

        home_pts = away_pts = 0
        notes = []
        if hr.get("available") and ar.get("available"):
            ppg_delta = hr.get("ppg", 0) - ar.get("ppg", 0)
            gd_delta = hr.get("gdpg", 0) - ar.get("gdpg", 0)
            if ppg_delta >= 0.50:
                home_pts += 1; notes.append(f"近期积分效率支持主队({hr.get('ppg',0):.2f}>{ar.get('ppg',0):.2f})")
            elif ppg_delta <= -0.50:
                away_pts += 1; notes.append(f"近期积分效率支持客队({ar.get('ppg',0):.2f}>{hr.get('ppg',0):.2f})")
            if gd_delta >= 0.50:
                home_pts += 1; notes.append(f"近期净胜球效率支持主队({hr.get('gdpg',0):+.2f}>{ar.get('gdpg',0):+.2f})")
            elif gd_delta <= -0.50:
                away_pts += 1; notes.append(f"近期净胜球效率支持客队({ar.get('gdpg',0):+.2f}>{hr.get('gdpg',0):+.2f})")

        if h2h.get("available") and h2h.get("matches", 0) >= 3:
            hw, aw = h2h.get("home_wins", 0), h2h.get("away_wins", 0)
            if hw > aw:
                home_pts += 1; notes.append(f"H2H支持主队({hw}胜 vs {aw}负)")
            elif aw > hw:
                away_pts += 1; notes.append(f"H2H支持客队({aw}胜 vs {hw}负)")
            if h2h.get("current_home_home_samples", 0) >= 2:
                n = h2h.get("current_home_home_samples", 0)
                w = h2h.get("current_home_home_wins", 0)
                if w / n >= 0.60:
                    home_pts += 1; notes.append(f"同主客场主队抵抗强({w}/{n}胜)")

        # 原模型方向是“客队不败”。主队基本面支持越多，冲突越大。
        net_home = home_pts - away_pts
        if net_home >= 2:
            level = "RED-高冲突"
        elif net_home == 1:
            level = "AMBER-中冲突"
        else:
            level = "GREEN-低冲突"
        available = bool(hr.get("available") or ar.get("available") or h2h.get("available"))
        summary = f"三层影子：{level}｜主支持{home_pts} 客支持{away_pts}"
        if notes:
            summary += "｜" + "；".join(notes)
        return {
            "mode": "shadow", "available": available, "level": level,
            "home_support_points": home_pts, "away_support_points": away_pts,
            "summary": summary, "notes": notes,
            "home_recent": hr, "away_recent": ar, "h2h": h2h,
            "source": ctx.get("url"),
        }
    except Exception as exc:
        base["error"] = repr(exc)
        return base


def evaluate(m):
    rows = [r for r in m.rows if any(k.lower() in r.company.lower() for k in PREFERRED)] or m.rows
    valid = [r for r in rows if r.x12_now_home and r.x12_now_away]
    ah = [r for r in valid if r.ah_now_line and r.ah_now_away is not None]
    reliable = len(valid) >= 4 and len(ah) >= 3

    half = [r for r in ah if _half(r.ah_now_line)]
    c1 = len(half) >= max(3, (len(ah) + 1) // 2)
    homes = [r.x12_now_home for r in valid]
    mh = median(homes) if homes else None
    c2 = mh is not None and 1.90 <= mh <= 2.10
    away_half = [r.ah_now_away + 1.0 for r in half if r.ah_now_away is not None]
    ma = median(away_half) if away_half else None
    c3 = ma is not None and 1.70 <= ma <= 1.85
    hmoves = [r.x12_now_home - r.x12_open_home for r in valid if r.x12_open_home]
    hmove = median(hmoves) if hmoves else None
    c4 = hmove is not None and hmove <= 0.03
    amoves = [r.x12_now_away - r.x12_open_away for r in valid if r.x12_open_away]
    amove = median(amoves) if amoves else None
    c5 = amove is not None and amove >= -0.02
    open75 = [r for r in ah if _threeq(r.ah_open_line)]
    now75 = [r for r in ah if _threeq(r.ah_now_line)]
    c6 = len(now75) == 0 and len(open75) <= max(1, len(ah) // 4)

    q_to_half = [r for r in ah if _quarter(r.ah_open_line) and _half(r.ah_now_line)]
    q_to_half_consensus = len(q_to_half) >= max(2, (len(ah) + 2) // 3)
    home_strong_drop = hmove is not None and hmove <= -0.15
    away_strong_rise = amove is not None and amove >= 0.20
    vetoes = []
    if q_to_half_consensus and home_strong_drop and away_strong_rise:
        vetoes.append(f"VETO-01 真强主队：主流平/半→半球（{len(q_to_half)}/{len(ah)}家），主胜中位变化{hmove:+.2f}，客胜中位变化{amove:+.2f}，欧亚同步强化主队")

    open_homes = [r.x12_open_home for r in valid if r.x12_open_home]
    mo_h = median(open_homes) if open_homes else None
    home_drop_pct = ((mo_h - mh) / mo_h) if mo_h and mh else None
    if c1 and home_drop_pct is not None and home_drop_pct >= 0.10 and amove is not None and amove >= 0.20 and not vetoes:
        vetoes.append(f"VETO-02 强压主队：主胜中位相对跌幅{home_drop_pct * 100:.1f}%，客胜中位变化{amove:+.2f}，当前主流仍为半球；先按真强/强支撑处理")
    vetoed = bool(vetoes)

    conds = [c1, c2, c3, c4, c5, c6]
    score = sum(conds)
    if not c1 or vetoed:
        reliable = False
    reasons = []
    if c1: reasons.append(f"当前主流-0.5（{len(half)}/{len(ah)}家）")
    if c2: reasons.append(f"主胜中位数{mh:.2f}处于1.90-2.10")
    if c3: reasons.append(f"客+0.5十进制中位价约{ma:.2f}")
    if c4: reasons.append(f"主胜整体维持/走低（中位变化{hmove:+.2f}）")
    if c5: reasons.append(f"客胜整体不降反升（中位变化{amove:+.2f}）")
    if c6: reasons.append("主流初/即时未形成-0.75共识")

    rep = next((r for key in ["36", "Crow", "18", "易", "伟"] for r in valid if key.lower() in r.company.lower()), valid[0] if valid else None)
    risk = "排除" if vetoed else ("中等" if score >= 6 else ("中等偏高" if score >= 5 else "高"))
    lean = "主队真实强化，禁止按半球假强推客" if vetoed else ("客队不败优先；可小幅考虑客胜冷门" if c3 and c4 and c5 and score >= 5 else "客队不败优先")

    # 三层升级：盘口层仍是唯一推送硬门槛；基本面/H2H先以shadow方式附加风险。
    shadow = _three_layer_shadow(m, score, c1 and not vetoed)
    if shadow.get("available"):
        lean = f"{lean}｜{shadow['level']}"
        if shadow["level"].startswith("RED") and not vetoed:
            risk = f"{risk}｜基本面高冲突"

    def x12(o):
        if not o: return "N/A"
        vals = [o.x12_open_home, o.x12_open_draw, o.x12_open_away, o.x12_now_home, o.x12_now_draw, o.x12_now_away]
        if any(v is None for v in vals): return "N/A"
        return f"{o.x12_open_home:.2f}/{o.x12_open_draw:.2f}/{o.x12_open_away:.2f} → {o.x12_now_home:.2f}/{o.x12_now_draw:.2f}/{o.x12_now_away:.2f}"

    def ahs(o):
        if not o or not o.ah_open_line or not o.ah_now_line: return "N/A"
        return f"{o.ah_open_home:.2f}/{o.ah_open_line}/{o.ah_open_away:.2f} → {o.ah_now_home:.2f}/{o.ah_now_line}/{o.ah_now_away:.2f}"

    return {
        "score": score, "reliable": reliable, "risk": risk, "lean": lean,
        "reasons": reasons, "vetoed": vetoed, "vetoes": vetoes,
        "rep": rep.company if rep else "N/A", "x12": x12(rep), "ah": ahs(rep),
        "away_half": ma, "home_move": hmove, "away_move": amove,
        "home_drop_pct": home_drop_pct, "quarter_to_half": len(q_to_half), "ah_count": len(ah),
        "three_layer": shadow,
    }
