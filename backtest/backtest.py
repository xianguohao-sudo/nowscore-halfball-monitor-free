"""Command line runner for the fixed -0.25 historical backtest."""
import argparse
import csv
import json
import time
from datetime import date, timedelta
from pathlib import Path
from statistics import median

from nowscore import fetch_match
from backtest.fetch_history import (
    HistoricalMatch,
    discover_history_sync,
    fetch_final_score,
)
from backtest.fingerprint_025 import evaluate_025


CONDITION_KEYS = [
    "home_price_2_00_2_20",
    "away_price_2_55_3_10",
    "away_quarter_low_water",
    "no_rise_to_half",
    "retreat_half_to_quarter",
    "home_price_not_compressed",
]


def parse_scores(raw):
    result = {}
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        match_id, score = part.strip().split("=", 1)
        home, away = score.split("-", 1)
        result[match_id.strip()] = (int(home), int(away))
    return result


def manual_matches(ids, scores):
    return [
        HistoricalMatch(
            match_id=match_id,
            match_date="manual",
            home_score=scores.get(match_id, (None, None))[0],
            away_score=scores.get(match_id, (None, None))[1],
        )
        for match_id in ids
    ]


def profit_quarter_away(home_score, away_score, hong_kong_water):
    if hong_kong_water is None:
        return None
    if away_score > home_score:
        return hong_kong_water
    if away_score == home_score:
        return hong_kong_water / 2.0
    return -1.0


def longest_losing_streak(items):
    longest = current = 0
    for item in items:
        if item["profit"] is not None and item["profit"] < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def percent(numerator, denominator):
    return 100.0 * numerator / denominator if denominator else 0.0


def fmt(value, digits=2):
    return "N/A" if value is None else f"{value:.{digits}f}"


def summarize(rows, threshold):
    selected = [
        row for row in rows
        if row["required_quarter"] and row["reliable"] and row["score"] >= threshold
    ]
    settled = [row for row in selected if row["home_score"] is not None]
    profits = [row["profit"] for row in settled if row["profit"] is not None]
    return {
        "threshold": threshold,
        "samples": len(settled),
        "away_unbeaten": sum(row["away_unbeaten"] for row in settled),
        "away_wins": sum(row["away_win"] for row in settled),
        "quarter_profitable": sum(
            row["profit"] is not None and row["profit"] > 0 for row in settled
        ),
        "roi": percent(sum(profits), len(profits)) if profits else None,
        "max_losing_streak": longest_losing_streak(settled),
    }


def build_report(meta, rows, errors):
    lines = [
        "# -0.25 假强主队历史回测报告",
        "",
        f"- 运行日期：{date.today().isoformat()}",
        f"- 数据区间：{meta['period']}",
        f"- 发现/指定比赛：{meta['requested']}",
        f"- 成功解析赔率：{len(rows)}",
        f"- 解析失败：{len(errors)}",
        f"- 初盘 -0.25 且数据可靠：{sum(r['required_quarter'] and r['reliable'] for r in rows)}",
        "",
        "规则固定为：初盘主流 -0.25 是必须条件，再对6项信号评分。"
        "本报告不会用赛后数据参与评分。",
        "",
        "| 阈值 | 有赛果样本 | 客队不败 | 客胜 | 客+0.25盈利 | ROI | 最长连续亏损 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for threshold in (4, 5, 6):
        item = summarize(rows, threshold)
        samples = item["samples"]
        lines.append(
            f"| ≥{threshold}项 | {samples} | "
            f"{item['away_unbeaten']} ({percent(item['away_unbeaten'], samples):.2f}%) | "
            f"{item['away_wins']} ({percent(item['away_wins'], samples):.2f}%) | "
            f"{item['quarter_profitable']} ({percent(item['quarter_profitable'], samples):.2f}%) | "
            f"{fmt(item['roi'])}% | {item['max_losing_streak']} |"
        )

    lines.extend([
        "",
        "## 样本明细",
        "",
        "| ID | 比赛 | 比分 | 评分 | 初主胜 | 初客胜 | 客+0.25水位 | 收益 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in rows:
        if not row["required_quarter"]:
            continue
        score_text = (
            f"{row['home_score']}-{row['away_score']}"
            if row["home_score"] is not None else "未知"
        )
        lines.append(
            f"| {row['match_id']} | {row['home']} vs {row['away']} | {score_text} | "
            f"{row['score']}/6 | {fmt(row['open_home'])} | {fmt(row['open_away'])} | "
            f"{fmt(row['away_water'])} | {fmt(row['profit'])} |"
        )

    if errors:
        lines.extend(["", "## 失败记录", ""])
        lines.extend(f"- {match_id}: {message}" for match_id, message in errors)

    lines.extend([
        "",
        "> +0.25结算：客胜为全赢，平局为半赢，主胜为全输；ROI按每场固定1单位计算。",
        "> 只有达到足够样本后才评估有效性，2场校验样本不能证明命中率。",
        "",
    ])
    return "\n".join(lines)


def run(args):
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    scores = parse_scores(args.scores)

    if args.match_ids:
        ids = [value.strip() for value in args.match_ids.split(",") if value.strip()]
        matches = manual_matches(ids, scores)
        period = "manual"
    else:
        end = date.fromisoformat(args.end_date) if args.end_date else date.today() - timedelta(days=1)
        start = (
            date.fromisoformat(args.start_date)
            if args.start_date else end - timedelta(days=args.days - 1)
        )
        matches = discover_history_sync(start, end)
        period = f"{start.isoformat()} ~ {end.isoformat()}"

    if args.max_matches > 0:
        matches = matches[:args.max_matches]
        period += f" (calibration limit={args.max_matches})"

    rows = []
    errors = []
    for index, historical in enumerate(matches, start=1):
        try:
            final_score = (
                (historical.home_score, historical.away_score)
                if historical.home_score is not None else fetch_final_score(historical.match_id)
            )
            match = fetch_match(historical.match_id)
            evaluation = evaluate_025(match)
            home_score, away_score = final_score if final_score else (None, None)
            profit = (
                profit_quarter_away(
                    home_score, away_score, evaluation["away_water"]
                )
                if home_score is not None else None
            )
            row = {
                "match_id": historical.match_id,
                "match_date": historical.match_date,
                "league": match.league,
                "kickoff": match.kickoff,
                "home": match.home,
                "away": match.away,
                "home_score": home_score,
                "away_score": away_score,
                "required_quarter": evaluation["required_quarter"],
                "reliable": evaluation["reliable"],
                "score": evaluation["score"],
                "open_home": evaluation["open_home"],
                "open_away": evaluation["open_away"],
                "away_water": evaluation["away_water"],
                "home_move": evaluation["home_move"],
                "retreat_count": evaluation["retreat_count"],
                "away_unbeaten": (
                    away_score >= home_score if home_score is not None else False
                ),
                "away_win": (
                    away_score > home_score if home_score is not None else False
                ),
                "profit": profit,
                **evaluation["conditions"],
            }
            rows.append(row)
            print(
                f"[{index}/{len(matches)}] {historical.match_id} "
                f"quarter={row['required_quarter']} score={row['score']}/6 "
                f"result={home_score}-{away_score}"
            )
        except Exception as exc:
            errors.append((historical.match_id, repr(exc)))
            print(f"[{index}/{len(matches)}] {historical.match_id} ERROR {exc!r}")
        if args.delay and index < len(matches):
            time.sleep(args.delay)

    fieldnames = [
        "match_id", "match_date", "league", "kickoff", "home", "away",
        "home_score", "away_score", "required_quarter", "reliable", "score",
        "open_home", "open_away", "away_water", "home_move", "retreat_count",
        *CONDITION_KEYS, "away_unbeaten", "away_win", "profit",
    ]
    with (output / "backtest_result.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    meta = {"period": period, "requested": len(matches)}
    report = build_report(meta, rows, errors)
    (output / "report.md").write_text(report, encoding="utf-8")
    (output / "run_meta.json").write_text(
        json.dumps(
            {"meta": meta, "parsed": len(rows), "errors": errors},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(report)
    return 0 if rows else 2


def parser():
    result = argparse.ArgumentParser(description="-0.25 fake-favourite backtest")
    result.add_argument("--match-ids", default="", help="comma-separated manual IDs")
    result.add_argument("--scores", default="", help="ID=home-away comma-separated")
    result.add_argument("--days", type=int, default=30)
    result.add_argument("--start-date", default="")
    result.add_argument("--end-date", default="")
    result.add_argument("--delay", type=float, default=0.35)
    result.add_argument("--max-matches", type=int, default=0)
    result.add_argument("--output-dir", default="data/backtest")
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
