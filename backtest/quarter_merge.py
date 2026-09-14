"""Merge quarter-ball shards, take 500 chronological samples and judge PH01-PH06."""
import argparse
import csv
import json
from datetime import datetime, date
from pathlib import Path

from quarter_classifier import CLASS_NAMES


def _bool(value):
    return str(value).strip().lower() in {"1", "true", "yes"}


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def longest_losing_streak(rows):
    longest = current = 0
    for row in rows:
        profit = _float(row.get("profit"))
        if profit is not None and profit < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def pct(n, d):
    return 100.0 * n / d if d else 0.0


def metrics(rows):
    settled = [row for row in rows if _float(row.get("profit")) is not None]
    profits = [_float(row["profit"]) for row in settled]
    hits = sum(_bool(row.get("target_hit")) for row in settled)
    return {
        "n": len(settled),
        "wins": sum(_bool(row.get("selected_win")) for row in settled),
        "draws": sum(_bool(row.get("draw")) for row in settled),
        "losses": sum(_bool(row.get("selected_loss")) for row in settled),
        "hit_rate": pct(hits, len(settled)),
        "roi": pct(sum(profits), len(profits)) if profits else None,
        "max_losing_streak": longest_losing_streak(settled),
    }


def status_for(item):
    n, hit, roi = item["n"], item["hit_rate"], item["roi"]
    if n < 40:
        return "INSUFFICIENT"
    if n >= 50 and hit >= 65.0 and roi is not None and roi > 5.0:
        return "KEEP-S"
    if hit >= 60.0 and roi is not None and roi > 0:
        return "KEEP-A"
    if hit >= 55.0:
        return "WATCH-B"
    return "DROP"


def chronological_key(row):
    kickoff = (row.get("kickoff") or "").strip()
    try:
        return (0, datetime.strptime(kickoff, "%Y-%m-%d %H:%M"))
    except ValueError:
        return (
            1,
            -_int(row.get("page_index")),
            -_int(row.get("row_order")),
            row.get("match_id", ""),
        )


def fmt(value):
    return "N/A" if value is None else f"{value:.2f}"


def make_report(rows, target, all_unique, errors, baseline_rows=0, shard_rows=0):
    split = max(1, int(len(rows) * 0.70))
    train = rows[:split]
    holdout = rows[split:]

    lines = [
        "# 平/半盘 PH01-PH06：500场冻结规则回测",
        "",
        f"- 运行日期：{date.today().isoformat()}",
        f"- 去重后归类样本：{all_unique}",
        f"- 本次正式样本：{len(rows)} / 目标 {target}",
        f"- 复用冻结基线样本：{baseline_rows}",
        f"- 本轮新增归类行：{shard_rows}",
        f"- 时间顺序训练/留出：{len(train)} / {len(holdout)}（规则不调参，只做稳定性检查）",
        f"- 本轮新增分片错误记录：{errors}",
        "- 通知阈值固定：评分 ≥7/10。",
        "",
        "判定标准：S=样本≥50、方向命中≥65%、ROI>5%；A=样本≥40、命中≥60%、ROI>0；B=样本≥40、命中≥55%；低于55%直接DROP。",
        "",
        "| 分类 | 方向逻辑 | ≥7样本 | 命中率 | ROI | 留出样本 | 留出命中 | 留出ROI | 最长连亏 | 结论 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    decisions = {}
    for class_id, class_name in CLASS_NAMES.items():
        full_rows = [r for r in rows if r["class_id"] == class_id and _int(r["score"]) >= 7]
        holdout_rows = [r for r in holdout if r["class_id"] == class_id and _int(r["score"]) >= 7]
        full = metrics(full_rows)
        ho = metrics(holdout_rows)
        status = status_for(full)
        direction_logic = "让球方胜" if class_id in {"PH01", "PH02"} else "受让方不败"
        decisions[class_id] = {
            "class_name": class_name,
            "direction_logic": direction_logic,
            "status": status,
            "enabled": status in {"KEEP-S", "KEEP-A"},
            "full": full,
            "holdout": ho,
        }
        lines.append(
            f"| {class_id} {class_name} | {direction_logic} | {full['n']} | "
            f"{full['hit_rate']:.2f}% | {fmt(full['roi'])}% | {ho['n']} | "
            f"{ho['hit_rate']:.2f}% | {fmt(ho['roi'])}% | "
            f"{full['max_losing_streak']} | {status} |"
        )

    lines.extend([
        "",
        "## 分类分布（全部评分）",
        "",
        "| 分类 | 全部样本 | ≥7 | ≥8 | ≥9 |",
        "|---|---:|---:|---:|---:|",
    ])
    for class_id, class_name in CLASS_NAMES.items():
        subset = [r for r in rows if r["class_id"] == class_id]
        lines.append(
            f"| {class_id} {class_name} | {len(subset)} | "
            f"{sum(_int(r['score']) >= 7 for r in subset)} | "
            f"{sum(_int(r['score']) >= 8 for r in subset)} | "
            f"{sum(_int(r['score']) >= 9 for r in subset)} |"
        )

    lines.extend([
        "",
        "## 结算说明",
        "",
        "- 让-0.25：赢球全赢，平局亏半，输球全输。",
        "- 受+0.25：赢球全赢，平局半赢，输球全输。",
        "- ROI按每场固定1单位、对应临场香港盘水位计算。",
        "- 基线与新增样本按 match_id 去重后，再按赔率页真实 kickoff 排序，只取最新500场。",
        "- 基线样本来自同一冻结PH01-PH06规则、4家公司、赛前最后‘即’口径，不重新调参。",
        "- PH03/PH06中的基本面/大小球信息缺失时，不伪造数据；只有页面实际抓到的赛前数据才计分。",
        "",
    ])
    return "\n".join(lines), decisions


def read_csv_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", default="data/shards")
    parser.add_argument("--target", type=int, default=500)
    parser.add_argument("--output-dir", default="data/quarter_backtest")
    parser.add_argument(
        "--baseline",
        default="",
        help="Optional previously frozen samples.csv to reuse before dedupe/time sorting.",
    )
    args = parser.parse_args()

    baseline = []
    if args.baseline:
        baseline_path = Path(args.baseline)
        if not baseline_path.exists():
            raise FileNotFoundError(f"baseline not found: {baseline_path}")
        baseline = read_csv_rows(baseline_path)
        print(f"loaded baseline rows={len(baseline)} from {baseline_path}")

    shard_rows = []
    error_count = 0
    for path in sorted(Path(args.input_root).rglob("*.csv")):
        shard_rows.extend(read_csv_rows(path))
    for path in sorted(Path(args.input_root).rglob("*.errors.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                error_count += len(payload.get("errors", []))
            elif isinstance(payload, list):
                error_count += len(payload)
        except Exception:
            pass

    unique = {}
    # Baseline first: when a same match is seen again, keep the already frozen
    # evidence rather than silently replacing it with a later network fetch.
    for row in baseline:
        unique.setdefault(row["match_id"], row)
    for row in shard_rows:
        unique.setdefault(row["match_id"], row)

    ordered = sorted(unique.values(), key=chronological_key)
    formal = ordered[-args.target:] if len(ordered) > args.target else ordered

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    if formal:
        fieldnames = list(formal[0].keys())
        with (output / "samples.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(formal)

    report, decisions = make_report(
        formal,
        args.target,
        len(ordered),
        error_count,
        baseline_rows=len(baseline),
        shard_rows=len(shard_rows),
    )
    (output / "report.md").write_text(report, encoding="utf-8")
    (output / "decision.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "meta.json").write_text(
        json.dumps(
            {
                "target": args.target,
                "baseline_rows": len(baseline),
                "new_shard_rows": len(shard_rows),
                "all_unique_classified": len(ordered),
                "formal_samples": len(formal),
                "errors": error_count,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(report)
    if len(formal) < args.target:
        print(f"WARNING: only {len(formal)} classified samples; target is {args.target}")
    return 0 if formal else 2


if __name__ == "__main__":
    raise SystemExit(main())
