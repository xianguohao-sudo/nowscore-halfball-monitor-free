from statistics import median

PREFERRED = ["36","Crow","易","伟","18","威","澳","利","盈","明"]

def _half(s):
    if not s: return False
    return s.replace(" ","") in {"半球","0.5","-0.5","半"}

def _threeq(s):
    if not s: return False
    return s.replace(" ","") in {"半/一","半一","0.75","-0.75"}

def evaluate(m):
    rows = [r for r in m.rows if any(k.lower() in r.company.lower() for k in PREFERRED)] or m.rows
    valid = [r for r in rows if r.x12_now_home and r.x12_now_away]
    ah = [r for r in valid if r.ah_now_line and r.ah_now_away is not None]
    reliable = len(valid) >= 4 and len(ah) >= 3

    half = [r for r in ah if _half(r.ah_now_line)]
    c1 = len(half) >= max(3, (len(ah)+1)//2)

    homes = [r.x12_now_home for r in valid]
    mh = median(homes) if homes else None
    c2 = mh is not None and 1.90 <= mh <= 2.10

    away_half = [r.ah_now_away + 1.0 for r in half if r.ah_now_away is not None]
    ma = median(away_half) if away_half else None
    c3 = ma is not None and 1.70 <= ma <= 1.85

    hmoves = [r.x12_now_home-r.x12_open_home for r in valid if r.x12_open_home]
    hmove = median(hmoves) if hmoves else None
    c4 = hmove is not None and hmove <= 0.03

    amoves = [r.x12_now_away-r.x12_open_away for r in valid if r.x12_open_away]
    amove = median(amoves) if amoves else None
    c5 = amove is not None and amove >= -0.02

    open75 = [r for r in ah if _threeq(r.ah_open_line)]
    now75 = [r for r in ah if _threeq(r.ah_now_line)]
    c6 = len(now75) == 0 and len(open75) <= max(1, len(ah)//4)

    conds = [c1,c2,c3,c4,c5,c6]
    score = sum(conds)
    if not c1:
        reliable = False

    reasons = []
    if c1: reasons.append(f"当前主流-0.5（{len(half)}/{len(ah)}家）")
    if c2: reasons.append(f"主胜中位数{mh:.2f}处于1.90-2.10")
    if c3: reasons.append(f"客+0.5十进制中位价约{ma:.2f}")
    if c4: reasons.append(f"主胜整体维持/走低（中位变化{hmove:+.2f}）")
    if c5: reasons.append(f"客胜整体不降反升（中位变化{amove:+.2f}）")
    if c6: reasons.append("主流初/即时未形成-0.75共识")

    rep = next((r for key in ["36","Crow","18","易","伟"] for r in valid if key.lower() in r.company.lower()), valid[0] if valid else None)
    risk = "中等" if score >= 6 else ("中等偏高" if score >= 5 else "高")
    lean = "客队不败优先；可小幅考虑客胜冷门" if c3 and c4 and c5 and score >= 5 else "客队不败优先"

    def x12(o):
        if not o: return "N/A"
        vals = [o.x12_open_home,o.x12_open_draw,o.x12_open_away,o.x12_now_home,o.x12_now_draw,o.x12_now_away]
        if any(v is None for v in vals): return "N/A"
        return f"{o.x12_open_home:.2f}/{o.x12_open_draw:.2f}/{o.x12_open_away:.2f} → {o.x12_now_home:.2f}/{o.x12_now_draw:.2f}/{o.x12_now_away:.2f}"
    def ahs(o):
        if not o or not o.ah_open_line or not o.ah_now_line: return "N/A"
        return f"{o.ah_open_home:.2f}/{o.ah_open_line}/{o.ah_open_away:.2f} → {o.ah_now_home:.2f}/{o.ah_now_line}/{o.ah_now_away:.2f}"

    return {
        "score": score, "reliable": reliable, "risk": risk, "lean": lean,
        "reasons": reasons, "rep": rep.company if rep else "N/A",
        "x12": x12(rep), "ah": ahs(rep), "away_half": ma,
    }
