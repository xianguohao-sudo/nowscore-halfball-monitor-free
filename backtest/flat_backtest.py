"""Backtest the six zero-line classes on 500 classified Nowscore matches."""
import argparse
import asyncio
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from flat_classifier import CLASS_NAMES, evaluate_flat
from nowscore import BASE, HEADERS, fetch_match


async def discover_recent_pages(max_pages):
    from playwright.async_api import async_playwright
    found = {}
    empty_streak = 0
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=HEADERS["User-Agent"])
        try:
            for index in range(1, max_pages + 1):
                await page.goto(
                    f"{BASE}/schedule.aspx?f=ft{index}",
                    wait_until="domcontentloaded", timeout=45000
                )
                await page.wait_for_timeout(2500)
                page_rows = await page.locator("tr").evaluate_all(
                    """trs => trs.map(tr => {
                        const text = (tr.innerText || '').trim();
                        const hrefs = Array.from(tr.querySelectorAll('a')).map(a => a.href || '');
                        let matchId = null;
                        for (const href of hrefs) {
                            const m = href.match(/(?:odds\/match\/|analysis\/|MatchDetail\/|id=)(\d+)/i);
                            if (m) { matchId = m[1]; break; }
                        }
                        if (!matchId) return null;
                        let score = null;
                        for (const node of Array.from(tr.querySelectorAll('.score,[class*="score"],[id*="score"]'))) {
                            const m = (node.textContent || '').trim().match(/^(\d{1,2})\s*[-:]\s*(\d{1,2})$/);
                            if (m) { score = [Number(m[1]), Number(m[2])]; break; }
                        }
                        if (!score) {
                            for (const token of text.split(/\s+/)) {
                                const m = token.match(/^(\d{1,2})-(\d{1,2})$/);
                                if (m) { score = [Number(m[1]), Number(m[2])]; break; }
                            }
                        }
                        return {matchId, score};
                    }).filter(Boolean)"""
                )
                before = len(found)
                for order, row in enumerate(page_rows):
                    if row.get("score") is None:
                        continue
                    found.setdefault(row["matchId"], {
                        "match_id": row["matchId"],
                        "home_score": row["score"][0],
                        "away_score": row["score"][1],
                        "page_index": index,
                        "row_order": order,
                    })
                added = len(found) - before
                print(f"[discover ft{index}] rows={len(page_rows)} unique_added={added} total={len(found)}")
                empty_streak = empty_streak + 1 if added == 0 else 0
                if index >= 7 and empty_streak >= 3:
                    break
        finally:
            await browser.close()
    return sorted(found.values(), key=lambda x: (x["page_index"], x["row_order"]))


def settle(selected, home_score, away_score, selected_handicap, water):
    if water is None:
        return None
    goal = home_score - away_score if selected == "home" else away_score - home_score
    if abs(selected_handicap) < 0.01:
        return water if goal > 0 else (0.0 if goal == 0 else -1.0)
    if abs(selected_handicap + 0.25) < 0.01:
        return water if goal > 0 else (-0.5 if goal == 0 else -1.0)
    if abs(selected_handicap - 0.25) < 0.01:
        return water if goal >= 0 else -1.0
    adjusted = goal + selected_handicap
    return water if adjusted > 0 else (0.0 if adjusted == 0 else -1.0)


def evaluate_one(item):
    match = fetch_match(item["match_id"])
    return item, match, evaluate_flat(match)


def longest_losing_streak(rows):
    longest = current = 0
    for row in rows:
        if row["profit"] is not None and row["profit"] < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def pct(n, d):
    return 100.0 * n / d if d else 0.0


def metrics(rows):
    settled = [row for row in rows if row["profit"] is not None]
    profits = [row["profit"] for row in settled]
    return {
        "n": len(settled),
        "wins": sum(row["selected_win"] for row in settled),
        "draws": sum(row["draw"] for row in settled),
        "losses": sum(row["selected_loss"] for row in settled),
        "non_loss_rate": pct(sum(not row["selected_loss"] for row in settled), len(settled)),
        "win_rate": pct(sum(row["selected_win"] for row in settled), len(settled)),
        "roi": pct(sum(profits), len(profits)) if profits else None,
        "max_losing_streak": longest_losing_streak(settled),
    }


def choose_and_validate(rows):
    split = max(1, int(len(rows) * 0.70))
    train_all, holdout_all = rows[:split], rows[split:]
    decisions = {}
    for class_id in CLASS_NAMES:
        candidates = []
        for threshold in (60, 70, 80):
            subset = [
                row for row in train_all
                if row["class_id"] == class_id and row["score"] >= threshold
            ]
            item = metrics(subset)
            if item["n"] >= 12 and item["roi"] is not None:
                candidates.append((item["roi"], item["n"], threshold, item))
        if candidates:
            _, _, threshold, train = max(candidates, key=lambda x: (x[0], x[1]))
        else:
            threshold = 70
            train = metrics([
                row for row in train_all
                if row["class_id"] == class_id and row["score"] >= threshold
            ])
        holdout = metrics([
            row for row in holdout_all
            if row["class_id"] == class_id and row["score"] >= threshold
        ])
        full = metrics([
            row for row in rows
            if row["class_id"] == class_id and row["score"] >= threshold
        ])
        keep = (
            class_id != "F1"
            and full["n"] >= 30 and holdout["n"] >= 12
            and train["roi"] is not None and train["roi"] > 0
            and holdout["roi"] is not None and holdout["roi"] > 0
            and full["roi"] is not None and full["roi"] >= 2.0
            and holdout["non_loss_rate"] >= 60.0
        )
        watch = (
            not keep and class_id != "F1" and full["n"] >= 20
            and full["roi"] is not None and full["roi"] > 0
        )
        decisions[class_id] = {
            "class_name": CLASS_NAMES[class_id],
            "threshold": threshold,
            "status": "KEEP" if keep else ("WATCH" if watch else "DROP"),
            "enabled": keep,
            "train": train,
            "holdout": holdout,
            "full": full,
        }
    return decisions, split


def fmt(value):
    return "N/A" if value is None else f"{value:.2f}"


def make_report(meta, rows, decisions, split, errors):
    lines = [
        "# 平手盘6分类：500场冻结规则回测", "",
        f"- 运行日期：{date.today().isoformat()}",
        f"- 历史页唯一完场比赛：{meta['discovered']}",
        f"- 成功解析赔率：{meta['parsed']}",
        f"- 成功归类样本：{len(rows)}（目标 {meta['target']}）",
        f"- 训练/留出集：{split}/{len(rows)-split}，按时间顺序70%/30%",
        f"- 解析失败：{len(errors)}", "",
        "规则在取赛果前冻结；阈值只用前70%训练集选择，后30%只做验证。",
        "KEEP要求总样本≥30、留出≥12、训练及留出ROI均为正、总ROI≥2%、留出不败率≥60%。",
        "",
        "| 类别 | 阈值 | 结论 | 总样本 | 总不败率 | 总ROI | 留出样本 | 留出不败率 | 留出ROI | 最长连亏 |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for class_id, decision in decisions.items():
        full, holdout = decision["full"], decision["holdout"]
        lines.append(
            f"| {class_id} {decision['class_name']} | ≥{decision['threshold']} | "
            f"{decision['status']} | {full['n']} | {full['non_loss_rate']:.2f}% | "
            f"{fmt(full['roi'])}% | {holdout['n']} | {holdout['non_loss_rate']:.2f}% | "
            f"{fmt(holdout['roi'])}% | {full['max_losing_streak']} |"
        )
    lines.extend([
        "", "## 规则解释", "",
        "- F1原生均势封顶59分，只做对照，不进入推送。",
        "- F2主队暗强、F3客队暗强；F4跟最终升盘方；F5反原让球方；F6跟穿零后的新让球方。",
        "- 加分来自公司数、盘口共识、方向方低水/降水、方向方胜赔下降、对手胜赔上升。",
        "- WATCH只积累样本，DROP停用；只有KEEP且达到本类阈值才允许实时通知。",
        "- ROI按当时亚洲盘水位和实际盘口结算；平手走盘，-0.25平局亏半。",
        "",
    ])
    return "\n".join(lines)


def run(args):
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    candidates = asyncio.run(discover_recent_pages(args.max_pages))
    rows, errors = [], []
    parsed = 0
    batch_size = max(args.workers * 8, 24)
    for start in range(0, len(candidates), batch_size):
        if len(rows) >= args.target:
            break
        batch = candidates[start:start + batch_size]
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(evaluate_one, item): item for item in batch}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    historical, match, result = future.result()
                    parsed += 1
                    if not result.get("classified"):
                        continue
                    selected = result["direction"]
                    selected_handicap = result["now_line"] if selected == "home" else -result["now_line"]
                    profit = settle(
                        selected, historical["home_score"], historical["away_score"],
                        selected_handicap, result["selected_now_water"]
                    )
                    hs, aws = historical["home_score"], historical["away_score"]
                    selected_win = hs > aws if selected == "home" else aws > hs
                    selected_loss = hs < aws if selected == "home" else aws < hs
                    rows.append({
                        "match_id": historical["match_id"],
                        "page_index": historical["page_index"],
                        "row_order": historical["row_order"],
                        "league": match.league, "kickoff": match.kickoff,
                        "home": match.home, "away": match.away,
                        "home_score": hs, "away_score": aws,
                        "class_id": result["class_id"], "class_name": result["class_name"],
                        "direction": selected, "score": result["score"],
                        "open_line": result["open_line"], "now_line": result["now_line"],
                        "open_consensus": result["open_consensus"],
                        "now_consensus": result["now_consensus"],
                        "selected_water": result["selected_now_water"],
                        "selected_x12_move": result["selected_x12_move"],
                        "other_x12_move": result["other_x12_move"],
                        "selected_win": selected_win, "draw": hs == aws,
                        "selected_loss": selected_loss, "profit": profit,
                    })
                except Exception as exc:
                    errors.append((item["match_id"], repr(exc)))
        print(f"[progress] scanned={min(start+len(batch),len(candidates))}/{len(candidates)} "
              f"parsed={parsed} classified={len(rows)} errors={len(errors)}")
        if args.delay:
            time.sleep(args.delay)

    rows.sort(key=lambda x: (-x["page_index"], x["row_order"], x["match_id"]))
    if len(rows) > args.target:
        rows = rows[-args.target:]
    decisions, split = choose_and_validate(rows)
    meta = {
        "discovered": len(candidates), "parsed": parsed,
        "classified": len(rows), "target": args.target, "max_pages": args.max_pages,
    }
    fields = [
        "match_id", "page_index", "row_order", "league", "kickoff", "home", "away",
        "home_score", "away_score", "class_id", "class_name", "direction", "score",
        "open_line", "now_line", "open_consensus", "now_consensus", "selected_water",
        "selected_x12_move", "other_x12_move", "selected_win", "draw",
        "selected_loss", "profit",
    ]
    with (output / "flat_500.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    report = make_report(meta, rows, decisions, split, errors)
    (output / "report.md").write_text(report, encoding="utf-8")
    (output / "validated_rules.json").write_text(
        json.dumps({
            "generated_at": date.today().isoformat(), "sample_size": len(rows),
            "split_index": split, "rules": decisions,
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "run_meta.json").write_text(
        json.dumps({"meta": meta, "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(report)
    return 0 if len(rows) >= args.target else 2


def parser():
    result = argparse.ArgumentParser(description="500-match zero-line class backtest")
    result.add_argument("--target", type=int, default=500)
    result.add_argument("--max-pages", type=int, default=14)
    result.add_argument("--workers", type=int, default=6)
    result.add_argument("--delay", type=float, default=0.05)
    result.add_argument("--output-dir", default="data/flat_backtest")
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
