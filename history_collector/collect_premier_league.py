import argparse
import asyncio
import csv
import json
import re
import sqlite3
import time
from pathlib import Path

from playwright.async_api import async_playwright

from nowscore import fetch_match

SEASON_URL = "https://info.nowscore.com/cn/League/2025-2026/36.html"
MATCH_ID_RE = re.compile(r"(?:analysis|odds|match|detail)[^\d]{0,20}(\d{6,9})", re.I)
ANY_ID_RE = re.compile(r"(?:matchid|scheduleid|id)\D{0,8}(\d{6,9})", re.I)
SCORE_RE = re.compile(r"^(\d{1,2})\s*[-:]\s*(\d{1,2})$")


def init_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY, league TEXT, season TEXT, round TEXT, kickoff_text TEXT,
        home TEXT, away TEXT, home_score INTEGER, away_score INTEGER, result TEXT,
        page_handicap TEXT, page_total TEXT, source_url TEXT,
        collected_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS odds (
        match_id TEXT NOT NULL, company TEXT NOT NULL,
        ah_open_home REAL, ah_open_line TEXT, ah_open_away REAL,
        ah_close_home REAL, ah_close_line TEXT, ah_close_away REAL,
        euro_open_home REAL, euro_open_draw REAL, euro_open_away REAL,
        euro_close_home REAL, euro_close_draw REAL, euro_close_away REAL,
        PRIMARY KEY(match_id, company)
    );
    CREATE TABLE IF NOT EXISTS failures (
        match_id TEXT PRIMARY KEY, error TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS odds_quality (
        match_id TEXT NOT NULL, company TEXT NOT NULL,
        close_candidate_valid INTEGER NOT NULL DEFAULT 0,
        reason TEXT,
        PRIMARY KEY(match_id, company)
    );
    """)
    return conn


def find_match_id(html: str):
    for rx in (MATCH_ID_RE, ANY_ID_RE):
        m = rx.search(html)
        if m:
            return m.group(1)
    ids = re.findall(r"\b(\d{7})\b", html)
    return ids[0] if ids else None


def clean_team(s):
    s = re.sub(r"\[[^\]]*\]", "", s or "")
    s = re.sub(r"\^\{[^}]*\}", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_row_text(cells):
    if len(cells) < 7:
        return None
    score_idx = next((i for i, x in enumerate(cells) if SCORE_RE.match(x.strip())), None)
    if score_idx is None or score_idx < 2 or score_idx + 2 >= len(cells):
        return None
    sm = SCORE_RE.match(cells[score_idx].strip())
    hs, aws = int(sm.group(1)), int(sm.group(2))
    home = clean_team(cells[score_idx - 1])
    away = clean_team(cells[score_idx + 2])
    if not home or not away:
        return None
    return {
        "round": cells[0].strip(),
        "kickoff_text": cells[1].strip() if len(cells) > 1 else "",
        "home": home, "away": away,
        "home_score": hs, "away_score": aws,
        "result": "H" if hs > aws else ("D" if hs == aws else "A"),
        "page_handicap": cells[score_idx + 3].strip() if score_idx + 3 < len(cells) else "",
        "page_total": cells[score_idx + 5].strip() if score_idx + 5 < len(cells) else "",
    }


async def parse_visible_matches(page, forced_round=None):
    found, debug = {}, []
    trs = page.locator("tr")
    for i in range(await trs.count()):
        tr = trs.nth(i)
        try:
            cells = [re.sub(r"\s+", " ", (await td.inner_text()).strip()) for td in await tr.locator("td").all()]
            parsed = parse_row_text(cells)
            if not parsed:
                continue
            html = await tr.inner_html()
            mid = find_match_id(html)
            debug.append({"round_requested": forced_round, "cells": cells, "match_id": mid})
            if mid:
                if forced_round is not None:
                    parsed["round"] = str(forced_round)
                found[mid] = {"match_id": mid, **parsed}
        except Exception:
            continue
    return found, debug


async def switch_round(page, round_no):
    """兼容 select / 第N轮链接两种页面实现。成功后返回 True。"""
    changed = await page.evaluate("""(roundNo) => {
      const norm = s => (s || '').replace(/\s+/g,'').replace('第','').replace('轮','');
      for (const s of document.querySelectorAll('select')) {
        const opts = [...s.options];
        const has1 = opts.some(o => norm(o.textContent) === '1');
        const has38 = opts.some(o => norm(o.textContent) === '38');
        if (!has1 || !has38) continue;
        const opt = opts.find(o => norm(o.textContent) === String(roundNo));
        if (opt) {
          s.value = opt.value;
          s.dispatchEvent(new Event('change', {bubbles:true}));
          return true;
        }
      }
      return false;
    }""", round_no)
    if not changed:
        candidates = page.locator("a,button,span,li,td")
        for i in range(min(await candidates.count(), 2500)):
            el = candidates.nth(i)
            try:
                if not await el.is_visible():
                    continue
                txt = re.sub(r"\s+", "", await el.inner_text())
                if txt in (str(round_no), f"第{round_no}轮"):
                    await el.click(timeout=2000)
                    changed = True
                    break
            except Exception:
                continue
    if changed:
        await page.wait_for_timeout(900)
    return changed


async def discover_matches(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="zh-CN", viewport={"width": 1600, "height": 1200})
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(5000)
        all_found, all_debug = {}, []
        round_counts = {}

        # 逐轮切换；不再只读取默认第38轮。
        for rnd in range(1, 39):
            ok = await switch_round(page, rnd)
            if not ok:
                round_counts[str(rnd)] = {"switch": False, "matches": 0}
                continue
            found, debug = await parse_visible_matches(page, rnd)
            all_found.update(found)
            all_debug.extend(debug)
            round_counts[str(rnd)] = {"switch": True, "matches": len(found)}
            print(f"发现轮次 {rnd:02d}: {len(found)} 场")

        # 若页面控件识别失败，至少保留默认页结果，便于继续诊断。
        if not all_found:
            found, debug = await parse_visible_matches(page)
            all_found.update(found)
            all_debug.extend(debug)

        Path("data/history").mkdir(parents=True, exist_ok=True)
        Path("data/history/discovery_debug.json").write_text(
            json.dumps({"round_counts": round_counts, "rows": all_debug}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        await browser.close()
        return list(all_found.values())


def save_match(conn, x):
    conn.execute("""
      INSERT INTO matches(match_id,league,season,round,kickoff_text,home,away,home_score,away_score,result,page_handicap,page_total,source_url)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(match_id) DO UPDATE SET round=excluded.round,kickoff_text=excluded.kickoff_text,
      home=excluded.home,away=excluded.away,home_score=excluded.home_score,away_score=excluded.away_score,
      result=excluded.result,page_handicap=excluded.page_handicap,page_total=excluded.page_total
    """, (x["match_id"], "英超", "2025-2026", x["round"], x["kickoff_text"], x["home"], x["away"],
          x["home_score"], x["away_score"], x["result"], x["page_handicap"], x["page_total"], SEASON_URL))


def close_quality(r):
    """当前历史详情页的 now 只能视为候选终值；异常/赛后污染一律隔离，不能进回测。"""
    vals = [r.x12_now_home, r.x12_now_draw, r.x12_now_away]
    if any(v is None for v in vals):
        return 0, "missing_euro_close"
    if any(v < 1.01 or v > 20 for v in vals):
        return 0, "suspicious_euro_close"
    ahvals = [r.ah_now_home, r.ah_now_away]
    if any(v is not None and (v < 0.20 or v > 2.50) for v in ahvals):
        return 0, "suspicious_ah_close"
    # 通过范围检查也不代表已证明是赛前终盘；正式回测仍需历史时间点证据。
    return 0, "unverified_pregame_timestamp"


def save_odds(conn, mid, m):
    for r in m.rows:
        conn.execute("INSERT OR REPLACE INTO odds VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            mid, r.company, r.ah_open_home, r.ah_open_line, r.ah_open_away,
            r.ah_now_home, r.ah_now_line, r.ah_now_away,
            r.x12_open_home, r.x12_open_draw, r.x12_open_away,
            r.x12_now_home, r.x12_now_draw, r.x12_now_away))
        valid, reason = close_quality(r)
        conn.execute("INSERT OR REPLACE INTO odds_quality(match_id,company,close_candidate_valid,reason) VALUES(?,?,?,?)",
                     (mid, r.company, valid, reason))


def export_csv(conn, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    for table in ("matches", "odds", "odds_quality", "failures"):
        cur = conn.execute(f"SELECT * FROM {table}")
        with (outdir / f"{table}.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow([d[0] for d in cur.description]); w.writerows(cur.fetchall())


def print_quality(conn):
    matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    odds_matches = conn.execute("SELECT COUNT(DISTINCT match_id) FROM odds").fetchone()[0]
    odds_rows = conn.execute("SELECT COUNT(*) FROM odds").fetchone()[0]
    failures = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
    ah = conn.execute("SELECT COUNT(*) FROM odds WHERE ah_open_line IS NOT NULL AND ah_close_line IS NOT NULL").fetchone()[0]
    euro = conn.execute("SELECT COUNT(*) FROM odds WHERE euro_open_home IS NOT NULL AND euro_close_home IS NOT NULL").fetchone()[0]
    suspicious = conn.execute("SELECT COUNT(*) FROM odds_quality WHERE reason LIKE 'suspicious_%'").fetchone()[0]
    unverified = conn.execute("SELECT COUNT(*) FROM odds_quality WHERE reason='unverified_pregame_timestamp'").fetchone()[0]
    print("=" * 70)
    print("2025-26 英超历史采集质量报告 V2")
    print("比赛:", matches, "/ 目标380")
    print("有赔率的比赛:", odds_matches)
    print("公司赔率记录:", odds_rows)
    print("亚洲盘初/候选终值都有:", ah)
    print("欧赔初/候选终值都有:", euro)
    print("明显污染候选终值:", suspicious)
    print("范围正常但缺少赛前时间戳证明:", unverified)
    print("正式回测可用终盘记录: 0（在找到赛前时间点证据前强制为0）")
    print("失败比赛:", failures)
    print("=" * 70)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="0=全部；穿透测试建议20")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--db", default="data/history/epl_2025_2026.sqlite")
    args = ap.parse_args()
    print("发现 2025-26 英超完场赛事:", SEASON_URL)
    matches = await discover_matches(SEASON_URL)
    matches.sort(key=lambda x: (int(x.get("round") or 999), int(x["match_id"])))
    print("发现带 Match ID 的完场赛事:", len(matches))
    if len(matches) < 300:
        print("WARNING: 仍低于完整赛季规模；禁止宣称380场完整。")
    if args.limit > 0:
        matches = matches[:args.limit]
        print("本轮测试限制:", len(matches), "场")
    conn = init_db(Path(args.db))
    for i, x in enumerate(matches, 1):
        mid = x["match_id"]; save_match(conn, x)
        try:
            m = fetch_match(mid); save_odds(conn, mid, m)
            conn.execute("DELETE FROM failures WHERE match_id=?", (mid,))
            print(f"[{i}/{len(matches)}] OK R{x['round']} {mid} {x['home']} {x['home_score']}-{x['away_score']} {x['away']} | 公司 {len(m.rows)}")
        except Exception as e:
            conn.execute("INSERT OR REPLACE INTO failures(match_id,error,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)", (mid, repr(e)))
            print(f"[{i}/{len(matches)}] FAIL {mid}: {e!r}")
        conn.commit()
        if args.delay: time.sleep(args.delay)
    export_csv(conn, Path("data/history/export")); print_quality(conn); conn.close()


if __name__ == "__main__":
    asyncio.run(main())
