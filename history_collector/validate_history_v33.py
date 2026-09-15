import argparse
import asyncio
import csv
import json
import random
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

OUT = Path("data/history/v33_quality")
LEAGUE_URL = "https://info.nowscore.com/cn/League/2025-2026/36.html"


def parse_kickoff(value: str):
    if not value:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            pass
    return None


def parse_change_time(raw: str, kickoff: datetime):
    if not raw or not kickoff:
        return None
    s = " ".join(raw.split())
    m = re.search(r"(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    month, day, hour, minute = map(int, m.groups())
    candidates = []
    for year in (kickoff.year - 1, kickoff.year, kickoff.year + 1):
        try:
            candidates.append(datetime(year, month, day, hour, minute))
        except ValueError:
            pass
    return min(candidates, key=lambda x: abs((x - kickoff).total_seconds())) if candidates else None


def fnum(v):
    try:
        return float(v)
    except Exception:
        return None


def market_from_header(cells):
    h = [x.replace(" ", "") for x in cells]
    if len(h) >= 7 and h[:7] == ["时", "比分", "主", "盘", "客", "变化", "状"]:
        return "AH"
    if len(h) >= 7 and h[:7] == ["时", "比分", "大", "盘", "小", "变化", "状"]:
        return "OU"
    if len(h) >= 7 and h[:7] == ["时", "比分", "主", "和局", "客", "变化", "状"]:
        return "1X2"
    return None


def parse_detail_html(html: str, match_id: str, company_id: str, kickoff: datetime, source_url: str):
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        header = [x.get_text(" ", strip=True) for x in trs[0].find_all(["th", "td"])]
        market = market_from_header(header)
        if not market:
            continue
        for tr in trs[1:]:
            c = [x.get_text(" ", strip=True) for x in tr.find_all(["th", "td"])]
            if len(c) < 7:
                continue
            dt = parse_change_time(c[5], kickoff)
            if not dt:
                continue
            status = c[6].strip()
            base = {
                "match_id": str(match_id), "company_id": str(company_id), "market": market,
                "change_time": dt.strftime("%Y-%m-%d %H:%M"), "minute": c[0], "score": c[1],
                "status": status, "source_url": source_url,
                "home": "", "line": "", "draw": "", "away": "", "over": "", "under": ""
            }
            if market == "AH":
                base.update(home=c[2], line=c[3], away=c[4])
            elif market == "OU":
                base.update(over=c[2], line=c[3], under=c[4])
            else:
                base.update(home=c[2], draw=c[3], away=c[4])

            pregame = dt < kickoff and status != "滚"
            usable = bool(pregame and (
                (market == "AH" and fnum(c[2]) is not None and c[3] not in ("", "封") and fnum(c[4]) is not None) or
                (market == "OU" and fnum(c[2]) is not None and c[3] not in ("", "封") and fnum(c[4]) is not None) or
                (market == "1X2" and fnum(c[2]) is not None and fnum(c[3]) is not None and fnum(c[4]) is not None)
            ))
            base["pregame"] = pregame
            base["usable"] = usable
            rows.append(base)
    rows.sort(key=lambda r: (r["market"], r["change_time"]))
    return rows


def range_quality(r):
    if not r["usable"]:
        return False, "not_usable"
    if r["market"] == "AH":
        h, a = fnum(r["home"]), fnum(r["away"])
        if h is None or a is None or not (0.01 <= h <= 5 and 0.01 <= a <= 5):
            return False, "range_ah"
    elif r["market"] == "1X2":
        vals = [fnum(r["home"]), fnum(r["draw"]), fnum(r["away"])]
        if any(v is None for v in vals) or not all(1.0 <= v <= 100 for v in vals):
            return False, "range_1x2"
    return True, "ok"


def choose_at_or_before(rows, target):
    valid = [r for r in rows if r["usable"] and datetime.strptime(r["change_time"], "%Y-%m-%d %H:%M") <= target]
    return max(valid, key=lambda r: r["change_time"]) if valid else None


async def discover_matches(page):
    found = {}
    await page.goto(LEAGUE_URL, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(3000)
    for rnd in range(1, 39):
        try:
            loc = page.get_by_text(str(rnd), exact=True)
            if await loc.count():
                await loc.first.click(timeout=5000)
                await page.wait_for_timeout(800)
        except Exception:
            pass
        soup = BeautifulSoup(await page.content(), "lxml")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            m = re.search(r"(?:analysis|odds|match|detail)[^0-9]{0,30}(\d{7,9})", href, re.I)
            if m:
                found[m.group(1)] = rnd
    return found


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--companies", type=int, default=17)
    ap.add_argument("--wait", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=20250915)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    reports, all_rows, snapshots = [], [], []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(locale="zh-CN", viewport={"width": 1600, "height": 1200})
        page = await context.new_page()
        discovered = await discover_matches(page)
        ids = list(discovered.keys())
        random.Random(args.seed).shuffle(ids)
        selected = ids[:args.count]
        print("V3.3 discovered", len(ids), "selected", selected)

        for idx, mid in enumerate(selected, 1):
            main_url = f"https://live.nowscore.com/odds/match/{mid}.htm"
            print(f"[{idx}/{len(selected)}] {mid}")
            try:
                await page.goto(main_url, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(args.wait * 1000)
                kickoff_raw = await page.locator("#hide_matchTime").get_attribute("value") if await page.locator("#hide_matchTime").count() else None
                kickoff = parse_kickoff(kickoff_raw)
                if not kickoff:
                    reports.append({"match_id": mid, "round": discovered[mid], "status": "FAIL", "reason": "kickoff_missing"})
                    continue

                hrefs = await page.locator('a[href*="3in1Odds.aspx"]').evaluate_all("els=>els.map(e=>e.getAttribute('href'))")
                details = []
                for href in hrefs:
                    if not href:
                        continue
                    full = urljoin(main_url, href)
                    m = re.search(r"companyid=(\d+).*?[?&]id=(\d+)", full, re.I)
                    if m and m.group(2) == mid:
                        item = (m.group(1), full)
                        if item not in details:
                            details.append(item)

                match_rows = []
                for cid, url in details[:args.companies]:
                    dp = await context.new_page()
                    try:
                        rr = await dp.goto(url, wait_until="domcontentloaded", timeout=90000)
                        await dp.wait_for_timeout(args.wait * 1000)
                        if rr and rr.status == 200:
                            parsed = parse_detail_html(await dp.content(), mid, cid, kickoff, url)
                            match_rows.extend(parsed)
                            all_rows.extend(parsed)
                    except Exception as e:
                        print(" detail error", cid, repr(e))
                    finally:
                        await dp.close()

                usable = [r for r in match_rows if r["usable"]]
                abnormal = []
                for r in usable:
                    ok, reason = range_quality(r)
                    if not ok:
                        x = dict(r); x["quality_reason"] = reason; abnormal.append(x)

                by_company = defaultdict(set)
                for r in usable:
                    if r["market"] in ("AH", "1X2"):
                        by_company[r["company_id"]].add(r["market"])
                ah_comp = {c for c, m in by_company.items() if "AH" in m}
                euro_comp = {c for c, m in by_company.items() if "1X2" in m}
                both = ah_comp & euro_comp

                contamination = sum(
                    1 for r in usable
                    if datetime.strptime(r["change_time"], "%Y-%m-%d %H:%M") >= kickoff or "滚" in r.get("status", "")
                )

                reasons = []
                if len(ah_comp) < 3: reasons.append("AH公司不足3")
                if len(euro_comp) < 3: reasons.append("1X2公司不足3")
                if len(both) < 3: reasons.append("AH+1X2共同公司不足3")
                if contamination: reasons.append("存在赛后/滚球污染")

                status = "PASS" if not reasons else "FAIL"
                if status == "PASS" and abnormal:
                    status = "WARN"

                for cid in sorted(both, key=lambda x: int(x)):
                    for market in ("AH", "1X2"):
                        rs = [r for r in usable if r["company_id"] == cid and r["market"] == market]
                        rs.sort(key=lambda r: r["change_time"])
                        if not rs:
                            continue
                        targets = [
                            ("OPEN", None),
                            ("T180", kickoff - timedelta(minutes=180)),
                            ("T60", kickoff - timedelta(minutes=60)),
                            ("T30", kickoff - timedelta(minutes=30)),
                            ("CLOSE", kickoff - timedelta(microseconds=1)),
                        ]
                        for label, target in targets:
                            s = rs[0] if label == "OPEN" else choose_at_or_before(rs, target)
                            if s:
                                z = dict(s); z["snapshot_type"] = label; z["kickoff"] = kickoff.strftime("%Y-%m-%d %H:%M"); snapshots.append(z)

                reports.append({
                    "match_id": mid, "round": discovered[mid], "kickoff": kickoff_raw, "status": status,
                    "detail_companies": len(details), "ah_companies": len(ah_comp), "euro_companies": len(euro_comp),
                    "both_companies": len(both), "usable_rows": len(usable), "abnormal_rows": len(abnormal),
                    "contamination": contamination, "reason": ";".join(reasons)
                })
                print(" ", status, "AH", len(ah_comp), "1X2", len(euro_comp), "BOTH", len(both), "usable", len(usable), "abnormal", len(abnormal))
            except Exception as e:
                reports.append({"match_id": mid, "round": discovered.get(mid), "status": "FAIL", "reason": repr(e)})

        await browser.close()

    def write_csv(path, rows):
        if not rows:
            return
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)

    write_csv(OUT / "quality_report.csv", reports)
    write_csv(OUT / "odds_timeline.csv", all_rows)
    write_csv(OUT / "odds_snapshots.csv", snapshots)
    (OUT / "quality_report.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")

    pcount = sum(r.get("status") == "PASS" for r in reports)
    wcount = sum(r.get("status") == "WARN" for r in reports)
    fcount = sum(r.get("status") == "FAIL" for r in reports)
    contaminated = sum(int(r.get("contamination", 0) or 0) for r in reports)
    print("=" * 78)
    print("V3.3 数据质量门禁 + 10场交叉验证")
    print("PASS", pcount, "WARN", wcount, "FAIL", fcount, "TOTAL", len(reports))
    print("赛前数据污染记录", contaminated)
    print("门禁建议: PASS+WARN>=9/10 且 contamination=0，才进入英超380场。")
    for r in reports:
        print(r.get("match_id"), "R", r.get("round"), r.get("status"), "AH", r.get("ah_companies"), "1X2", r.get("euro_companies"), "BOTH", r.get("both_companies"), r.get("reason", ""))
    print("输出:", OUT)
    print("=" * 78)

if __name__ == "__main__":
    asyncio.run(main())
