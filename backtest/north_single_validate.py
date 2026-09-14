"""Independent Beijing Single (北单) validation for frozen PH quarter-ball rules.

Source separation is deliberate:
- Okooo decides WHICH matches belong to Beijing Single.
- NowScore supplies canonical match ids, pre-match Asian/1X2 prices and scores.
- Frozen `quarter_classifier.evaluate_quarter` decides PH01-PH06.

No fuzzy match is accepted unless it is unique and above strict confidence gates.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional

from backtest.quarter_shard import FIELDNAMES, evaluate_item
from nowscore import BASE, HEADERS

OKOOO = "https://www.okooo.cn/livecenter/danchang/"


@dataclass
class NorthSingleMatch:
    issue: str
    seq: str
    kickoff: str
    league: str
    home: str
    away: str
    home_score: int
    away_score: int
    source_url: str


@dataclass
class NowScoreRow:
    match_id: str
    kickoff: str
    league: str
    home: str
    away: str
    home_score: int
    away_score: int


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def normalize_team(text: str) -> str:
    text = unicodedata.normalize("NFKC", _clean(text)).lower()
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\([^)]*[+-]\s*\d+[^)]*\)", "", text)
    text = re.sub(r"(?:\+1|-1)$", "", text)
    text = re.sub(r"\b(?:fc|cf|sc|ac|afc|u\d{2}|u\d{1})\b", "", text)
    text = re.sub(r"[\s·•.．,，'’\-_/（）()]+", "", text)
    return text


def team_similarity(left: str, right: str) -> float:
    a, b = normalize_team(left), normalize_team(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 3 and (a in b or b in a):
        return 0.93
    ratio = SequenceMatcher(None, a, b).ratio()
    chars_a, chars_b = set(a), set(b)
    jaccard = len(chars_a & chars_b) / max(1, len(chars_a | chars_b))
    return max(ratio, jaccard)


def _parse_score(text: str):
    match = re.fullmatch(r"\s*(\d{1,2})\s*[-:]\s*(\d{1,2})\s*", text or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _parse_datetime_blob(blob: str, issue_year: int = 2026) -> Optional[datetime]:
    blob = _clean(blob)
    patterns = [
        r"(?P<y>20\d{2})[-/.年](?P<m>\d{1,2})[-/.月](?P<d>\d{1,2})[日\sT]+(?P<h>\d{1,2}):(?P<mi>\d{2})",
        r"(?P<m>\d{1,2})[-/.月](?P<d>\d{1,2})[日\s]+(?P<h>\d{1,2}):(?P<mi>\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, blob)
        if not match:
            continue
        groups = match.groupdict()
        year = int(groups.get("y") or issue_year)
        try:
            return datetime(year, int(groups["m"]), int(groups["d"]), int(groups["h"]), int(groups["mi"]))
        except ValueError:
            continue
    return None


def _meaningful_team_cell(text: str) -> bool:
    text = _clean(text)
    if len(text) < 2:
        return False
    if _parse_score(text):
        return False
    if re.fullmatch(r"\d{1,3}", text):
        return False
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return False
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return False
    if text in {"完", "完场", "未开始", "延期", "取消", "腰斩", "中断", "情报", "析", "亚", "欧"}:
        return False
    return True


def parse_okooo_dom_rows(issue: str, url: str, dom_rows: list[dict], page_text: str) -> list[NorthSingleMatch]:
    """Parse completed 北单 rows from Okooo DOM snapshots.

    Okooo has changed markup several times, so parsing intentionally uses cell
    semantics rather than brittle CSS class names.
    """
    results = []
    seen = set()

    for raw in dom_rows:
        cells = [_clean(value) for value in raw.get("cells", []) if _clean(value)]
        text = _clean(raw.get("text", ""))
        blob = " | ".join(cells + [text, raw.get("attrs", "")])
        score_index = next((i for i, value in enumerate(cells) if _parse_score(value)), None)
        if score_index is None:
            continue
        score = _parse_score(cells[score_index])
        dt = _parse_datetime_blob(blob)
        if dt is None:
            continue

        before = [value for value in cells[:score_index] if _meaningful_team_cell(value)]
        after = [value for value in cells[score_index + 1:] if _meaningful_team_cell(value)]
        if not before or not after:
            continue
        home = before[-1]
        away = after[0]

        # Reject obvious non-team/status columns that slipped through.
        if len(normalize_team(home)) < 2 or len(normalize_team(away)) < 2:
            continue

        seq_match = re.search(r"(?<!\d)(\d{3})(?!\d)", " ".join(cells[: max(1, score_index)]))
        seq = seq_match.group(1) if seq_match else ""
        league = before[-2] if len(before) >= 2 and before[-2] != home else ""
        key = (dt.strftime("%Y-%m-%d %H:%M"), normalize_team(home), normalize_team(away), score)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            NorthSingleMatch(
                issue=issue,
                seq=seq,
                kickoff=dt.strftime("%Y-%m-%d %H:%M"),
                league=league,
                home=home,
                away=away,
                home_score=score[0],
                away_score=score[1],
                source_url=url,
            )
        )

    return results


OKOOO_ROW_JS = r"""trs => trs.map(tr => {
    const cells = Array.from(tr.querySelectorAll('th,td')).map(td => (td.innerText || td.textContent || '').trim());
    const attrs = [tr, ...Array.from(tr.querySelectorAll('th,td'))].map(el =>
        Array.from(el.attributes || []).map(a => `${a.name}=${a.value}`).join(' ')
    ).join(' ');
    return {text: (tr.innerText || tr.textContent || '').trim(), cells, attrs};
}).filter(x => x.text)"""


async def fetch_okooo_issue(page, issue: str) -> list[NorthSingleMatch]:
    url = f"{OKOOO}?date={issue}"
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(2500)
    body = await page.locator("body").inner_text()
    # Critical anti-contamination gate: invalid issue urls often redirect/show another issue.
    if issue not in body:
        print(f"[okooo {issue}] requested issue marker absent; reject page url={page.url}")
        return []
    rows = await page.locator("tr").evaluate_all(OKOOO_ROW_JS)
    parsed = parse_okooo_dom_rows(issue, page.url, rows, body)
    print(f"[okooo {issue}] dom_rows={len(rows)} completed_parsed={len(parsed)} url={page.url}")
    return parsed


NOWSCORE_ROW_JS = r"""trs => trs.map(tr => {
    const rowId = (tr.id || '').trim();
    const m = rowId.match(/^tr1_(\d{6,10})$/i);
    if (!m) return null;
    const cells = Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || td.textContent || '').trim());
    const text = (tr.innerText || tr.textContent || '').trim();
    let score = null, scoreIndex = -1;
    for (let i = 0; i < cells.length; i++) {
        const sm = cells[i].match(/^(\d{1,2})\s*[-:]\s*(\d{1,2})$/);
        if (sm) { score = [Number(sm[1]), Number(sm[2])]; scoreIndex = i; break; }
    }
    if (!score) {
        const sm = text.match(/(?:^|\s)(\d{1,2})\s*[-:]\s*(\d{1,2})(?:\s|$)/);
        if (sm) score = [Number(sm[1]), Number(sm[2])];
    }
    const anchors = Array.from(tr.querySelectorAll('a')).map(a => ({
        text: (a.innerText || a.textContent || '').trim(),
        href: a.getAttribute('href') || '',
        onclick: a.getAttribute('onclick') || ''
    }));
    return {matchId: m[1], cells, text, score, scoreIndex, anchors};
}).filter(Boolean)"""


def _infer_nowscore_names(raw: dict):
    cells = [_clean(x) for x in raw.get("cells", [])]
    score_index = int(raw.get("scoreIndex", -1))
    if score_index >= 0:
        before = [x for x in cells[:score_index] if _meaningful_team_cell(x)]
        after = [x for x in cells[score_index + 1:] if _meaningful_team_cell(x)]
        if before and after:
            return before[-1], after[0]

    # Fallback to anchor texts, excluding odds/action links and numeric labels.
    names = []
    for anchor in raw.get("anchors", []):
        text = _clean(anchor.get("text", ""))
        href = (anchor.get("href", "") + " " + anchor.get("onclick", "")).lower()
        if not _meaningful_team_cell(text):
            continue
        if any(token in href for token in ("odds", "analysis", "detail", "asian", "europe")):
            continue
        if text not in names:
            names.append(text)
    return (names[-2], names[-1]) if len(names) >= 2 else ("", "")


def _infer_nowscore_time(raw: dict, day: date):
    blob = " | ".join(raw.get("cells", [])) + " | " + raw.get("text", "")
    full = _parse_datetime_blob(blob)
    if full:
        return full
    match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", blob)
    if match:
        try:
            return datetime(day.year, day.month, day.day, int(match.group(1)), int(match.group(2)))
        except ValueError:
            pass
    return None


async def fetch_nowscore_day(page, day: date) -> list[NowScoreRow]:
    await page.goto(f"{BASE}/schedule.aspx?date={day:%Y-%m-%d}", wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(1800)
    rows = await page.locator("tr").evaluate_all(NOWSCORE_ROW_JS)
    parsed = []
    for raw in rows:
        score = raw.get("score")
        if not score:
            continue
        home, away = _infer_nowscore_names(raw)
        kickoff = _infer_nowscore_time(raw, day)
        if not home or not away or kickoff is None:
            continue
        cells = [_clean(x) for x in raw.get("cells", []) if _clean(x)]
        league = cells[0] if cells else ""
        parsed.append(
            NowScoreRow(
                match_id=str(raw["matchId"]),
                kickoff=kickoff.strftime("%Y-%m-%d %H:%M"),
                league=league,
                home=home,
                away=away,
                home_score=int(score[0]),
                away_score=int(score[1]),
            )
        )
    print(f"[nowscore {day}] canonical_completed={len(parsed)}")
    return parsed


def mapping_score(north: NorthSingleMatch, now: NowScoreRow):
    try:
        a = datetime.strptime(north.kickoff, "%Y-%m-%d %H:%M")
        b = datetime.strptime(now.kickoff, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    minutes = abs((a - b).total_seconds()) / 60
    if minutes > 15:
        return None
    if (north.home_score, north.away_score) != (now.home_score, now.away_score):
        return None
    home_sim = team_similarity(north.home, now.home)
    away_sim = team_similarity(north.away, now.away)
    if home_sim < 0.55 or away_sim < 0.55:
        return None
    # Team identity dominates; time/score are hard gates above.
    confidence = (home_sim + away_sim) / 2
    return confidence, home_sim, away_sim, minutes


def map_matches(north_matches: list[NorthSingleMatch], now_by_day: dict[str, list[NowScoreRow]]):
    mapped = []
    unmatched = []
    ambiguous = []
    for north in north_matches:
        day = north.kickoff[:10]
        candidates = []
        for now in now_by_day.get(day, []):
            score = mapping_score(north, now)
            if score is not None:
                candidates.append((score[0], score, now))
        candidates.sort(key=lambda x: x[0], reverse=True)
        if not candidates:
            unmatched.append(asdict(north))
            continue
        best_conf, detail, best = candidates[0]
        second_conf = candidates[1][0] if len(candidates) > 1 else -1.0
        # Strict unique mapping: high confidence and a meaningful gap to runner-up.
        if best_conf < 0.72 or (second_conf >= 0 and best_conf - second_conf < 0.10):
            ambiguous.append({
                "north": asdict(north),
                "best": asdict(best),
                "best_confidence": best_conf,
                "second_confidence": second_conf,
            })
            continue
        mapped.append({
            "match_id": best.match_id,
            "home_score": best.home_score,
            "away_score": best.away_score,
            "match_date": day,
            "source": "okooo-north-single",
            "issue": north.issue,
            "seq": north.seq,
            "north_home": north.home,
            "north_away": north.away,
            "nowscore_home": best.home,
            "nowscore_away": best.away,
            "kickoff": best.kickoff,
            "mapping_confidence": round(best_conf, 4),
            "mapping_home_similarity": round(detail[1], 4),
            "mapping_away_similarity": round(detail[2], 4),
            "mapping_minutes": detail[3],
        })
    return mapped, unmatched, ambiguous


def _metrics(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "hit_rate": 0.0, "roi": None}
    hits = sum(str(row.get("target_hit", "")).lower() in {"1", "true"} for row in rows)
    profits = [float(row["profit"]) for row in rows if row.get("profit") not in (None, "")]
    return {
        "n": n,
        "hit_rate": round(hits / n * 100, 2),
        "roi": round(sum(profits) / n * 100, 2) if len(profits) == n else None,
    }


def _decision(metrics):
    if metrics["n"] < 40:
        return "INSUFFICIENT"
    if metrics["hit_rate"] >= 60.0 and metrics["roi"] is not None and metrics["roi"] > 0:
        return "PASS-INDEPENDENT"
    return "FAIL-INDEPENDENT"


async def discover_and_map(issues: list[str], cutoff: datetime):
    from playwright.async_api import async_playwright

    north_matches = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=HEADERS["User-Agent"], locale="zh-CN")
        page = await context.new_page()
        try:
            for issue in issues:
                try:
                    north_matches.extend(await fetch_okooo_issue(page, issue))
                except Exception as exc:
                    print(f"[okooo {issue}] ERROR {exc!r}")

            dedup = {}
            for item in north_matches:
                try:
                    dt = datetime.strptime(item.kickoff, "%Y-%m-%d %H:%M")
                except ValueError:
                    continue
                if dt >= cutoff:
                    continue
                key = (item.kickoff, normalize_team(item.home), normalize_team(item.away), item.home_score, item.away_score)
                dedup[key] = item
            north_matches = sorted(dedup.values(), key=lambda x: x.kickoff)
            print(f"north_single unique completed before cutoff={len(north_matches)}")

            days = sorted({datetime.strptime(item.kickoff, "%Y-%m-%d %H:%M").date() for item in north_matches})
            now_by_day = {}
            for day in days:
                try:
                    now_by_day[day.isoformat()] = await fetch_nowscore_day(page, day)
                except Exception as exc:
                    print(f"[nowscore {day}] ERROR {exc!r}")
                    now_by_day[day.isoformat()] = []
        finally:
            await browser.close()

    return north_matches, map_matches(north_matches, now_by_day)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--issues", default="26081,26083,26085,26088,26089,26092")
    parser.add_argument("--cutoff", default="2026-09-05 17:00")
    parser.add_argument("--companies", type=int, default=4)
    parser.add_argument("--output-dir", default="data/north_single_validation")
    args = parser.parse_args()

    issues = [value.strip() for value in args.issues.split(",") if value.strip()]
    cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d %H:%M")
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    north_matches, mapping = asyncio.run(discover_and_map(issues, cutoff))
    mapped, unmatched, ambiguous = mapping
    print(
        f"mapping summary north={len(north_matches)} mapped={len(mapped)} "
        f"unmatched={len(unmatched)} ambiguous={len(ambiguous)}"
    )

    (out / "north_single.json").write_text(json.dumps([asdict(x) for x in north_matches], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "mapped.json").write_text(json.dumps(mapped, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "unmatched.json").write_text(json.dumps(unmatched, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "ambiguous.json").write_text(json.dumps(ambiguous, ensure_ascii=False, indent=2), encoding="utf-8")

    evaluated = []
    eval_errors = []
    skip_counts = Counter()
    for pos, item in enumerate(mapped, 1):
        try:
            row, diagnostic = evaluate_item(item, companies=args.companies)
            if row is None:
                skip_counts[(diagnostic or {}).get("reason", "unknown")] += 1
                continue
            row["issue"] = item.get("issue", "")
            row["seq"] = item.get("seq", "")
            row["mapping_confidence"] = item.get("mapping_confidence", "")
            evaluated.append(row)
            print(f"[eval {pos}/{len(mapped)}] {item['match_id']} {row['class_id']} score={row['score']}")
        except Exception as exc:
            eval_errors.append({"match_id": item.get("match_id"), "error": repr(exc)})
            print(f"[eval {pos}/{len(mapped)}] {item.get('match_id')} ERROR {exc!r}")

    write_csv(out / "classified.csv", evaluated, FIELDNAMES + ["issue", "seq", "mapping_confidence"])
    (out / "eval_errors.json").write_text(json.dumps(eval_errors, ensure_ascii=False, indent=2), encoding="utf-8")

    formal = [row for row in evaluated if int(row.get("score", 0)) >= 7]
    ph04_7 = [row for row in formal if row.get("class_id") == "PH04"]
    ph04_9 = [row for row in evaluated if row.get("class_id") == "PH04" and int(row.get("score", 0)) >= 9]
    m7, m9 = _metrics(ph04_7), _metrics(ph04_9)
    decision = _decision(m9)

    mapping_rate = len(mapped) / len(north_matches) * 100 if north_matches else 0.0
    report = f"""# 北单-only PH04 独立验证\n\n- 数据资格源：澳客北单\n- 赔率源：NowScore 赛前4家公司 `即` 盘\n- 冻结分类器：PH01-PH06 V1.0（未改规则）\n- 验证截止：{args.cutoff}（严格早于正式500场样本起点）\n- 北单期号：{', '.join(issues)}\n- 北单已完赛解析：{len(north_matches)}\n- 唯一高置信映射：{len(mapped)}（{mapping_rate:.2f}%）\n- 未匹配：{len(unmatched)}\n- 歧义拒绝：{len(ambiguous)}\n- 完成PH分类：{len(evaluated)}\n- 赔率评估错误：{len(eval_errors)}\n- 跳过：{dict(skip_counts)}\n\n## PH04 独立结果\n\n| 阈值 | N | 方向命中率 | ROI |\n|---|---:|---:|---:|\n| PH04 >=7 | {m7['n']} | {m7['hit_rate']:.2f}% | {('N/A' if m7['roi'] is None else f"{m7['roi']:.2f}%")} |\n| PH04 >=9 | {m9['n']} | {m9['hit_rate']:.2f}% | {('N/A' if m9['roi'] is None else f"{m9['roi']:.2f}%")} |\n\n**PH04>=9独立裁决：{decision}**\n\n启用门槛保持冻结：独立北单样本 `N>=40`、命中率 `>=60%`、ROI `>0`。未达到三项全部条件前，继续 WATCH-B，禁止微信推送。\n"""
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "decision.json").write_text(json.dumps({"PH04_ge7": m7, "PH04_ge9": m9, "decision": decision}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)

    # Data-quality gate: a tiny mapping rate means the parser/mapping needs work;
    # fail loudly instead of publishing a deceptively clean football conclusion.
    if north_matches and mapping_rate < 50.0:
        print(f"FATAL: mapping coverage too low: {mapping_rate:.2f}% < 50%")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
