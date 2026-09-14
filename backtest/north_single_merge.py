"""Merge North-Single validation shards and independently judge frozen PH04."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def read_rows(root: Path):
    rows = []
    for path in sorted(root.rglob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    dedup = {}
    for row in rows:
        match_id = str(row.get("match_id", "")).strip()
        if match_id:
            dedup[match_id] = row
    return list(dedup.values())


def truth(value):
    return str(value).strip().lower() in {"1", "true", "yes"}


def metrics(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "hit_rate": 0.0, "roi": None, "max_losing_streak": 0}
    hits = sum(truth(row.get("target_hit")) for row in rows)
    profits = [float(row["profit"]) for row in rows if row.get("profit") not in (None, "")]
    streak = max_streak = 0
    for row in rows:
        if float(row.get("profit") or 0) < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {
        "n": n,
        "hit_rate": round(hits / n * 100, 2),
        "roi": round(sum(profits) / n * 100, 2) if len(profits) == n else None,
        "max_losing_streak": max_streak,
    }


def decision(m):
    if m["n"] < 40:
        return "INSUFFICIENT"
    if m["hit_rate"] >= 60.0 and m["roi"] is not None and m["roi"] > 0:
        return "PASS-INDEPENDENT"
    return "FAIL-INDEPENDENT"


def fmt_roi(value):
    return "N/A" if value is None else f"{value:.2f}%"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", default="data/north_shards")
    parser.add_argument("--mapping", default="data/mapping/north_single_mapping.json")
    parser.add_argument("--output-dir", default="data/north_single_validation")
    args = parser.parse_args()

    rows = read_rows(Path(args.input_root))
    mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Keep deterministic chronological order for auditability.
    rows.sort(key=lambda r: (r.get("kickoff", ""), r.get("match_id", "")))
    if rows:
        fieldnames = list(rows[0].keys())
        with (out / "classified.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    by_class = defaultdict(list)
    for row in rows:
        by_class[row.get("class_id", "")].append(row)

    class_metrics = {}
    for cid in ("PH01", "PH02", "PH03", "PH04", "PH05", "PH06"):
        ge7 = [r for r in by_class[cid] if int(float(r.get("score") or 0)) >= 7]
        class_metrics[cid] = metrics(ge7)

    ph04_7 = [r for r in by_class["PH04"] if int(float(r.get("score") or 0)) >= 7]
    ph04_8 = [r for r in by_class["PH04"] if int(float(r.get("score") or 0)) >= 8]
    ph04_9 = [r for r in by_class["PH04"] if int(float(r.get("score") or 0)) >= 9]
    m7, m8, m9 = metrics(ph04_7), metrics(ph04_8), metrics(ph04_9)
    verdict = decision(m9)

    # Critical audit guard: this validation must stay strictly before the formal
    # 500-sample period. Discovery already applies the cutoff; verify again here.
    cutoff = mapping.get("cutoff", "2026-09-05 00:00")
    overlap = [r for r in rows if r.get("kickoff", "") >= cutoff]
    if overlap:
        print(f"FATAL: {len(overlap)} classified rows violate independent cutoff {cutoff}")
        return 4

    report = f"""# 北单-only PH04 独立验证报告\n\n## 数据质量\n\n- 北单资格源：澳客 Okooo\n- 赔率源：NowScore 赛前4家公司 `即` 盘\n- 冻结分类器：PH01-PH06 V1.0（本轮未改规则）\n- 独立样本截止：`{cutoff}`\n- 澳客期号：{', '.join(mapping.get('issues', []))}\n- 澳客已完赛解析：{mapping.get('north_parsed', 0)}\n- 唯一高置信 NowScore 映射：{mapping.get('mapped', 0)}\n- 映射率：{mapping.get('mapping_rate', 0):.2f}%\n- 未匹配：{len(mapping.get('unmatched', []))}\n- 歧义拒绝：{len(mapping.get('ambiguous', []))}\n- 实际完成平/半分类：{len(rows)}\n\n## 各分类 score>=7\n\n| 分类 | N | 命中率 | ROI | 最长亏损连场 |\n|---|---:|---:|---:|---:|\n"""
    for cid in ("PH01", "PH02", "PH03", "PH04", "PH05", "PH06"):
        m = class_metrics[cid]
        report += f"| {cid} | {m['n']} | {m['hit_rate']:.2f}% | {fmt_roi(m['roi'])} | {m['max_losing_streak']} |\n"

    report += f"""\n## PH04 预定义强度分档\n\n| 阈值 | N | 命中率 | ROI | 最长亏损连场 |\n|---|---:|---:|---:|---:|\n| PH04 >=7 | {m7['n']} | {m7['hit_rate']:.2f}% | {fmt_roi(m7['roi'])} | {m7['max_losing_streak']} |\n| PH04 >=8 | {m8['n']} | {m8['hit_rate']:.2f}% | {fmt_roi(m8['roi'])} | {m8['max_losing_streak']} |\n| **PH04 >=9** | **{m9['n']}** | **{m9['hit_rate']:.2f}%** | **{fmt_roi(m9['roi'])}** | **{m9['max_losing_streak']}** |\n\n## 独立裁决\n\n**{verdict}**\n\n冻结启用门槛：`PH04>=9` 在独立北单样本必须同时满足 **N>=40、方向命中率>=60%、ROI>0**。\n\n- `PASS-INDEPENDENT`：才有资格进入实盘通知候选；仍需明确改 policy 才会推送。\n- `FAIL-INDEPENDENT`：维持 WATCH-B，不推送。\n- `INSUFFICIENT`：继续向更早北单期号扩样，不改分类条件。\n"""

    (out / "report.md").write_text(report, encoding="utf-8")
    decision_obj = {
        "cutoff": cutoff,
        "mapping": {
            "north_parsed": mapping.get("north_parsed", 0),
            "mapped": mapping.get("mapped", 0),
            "mapping_rate": mapping.get("mapping_rate", 0),
            "unmatched": len(mapping.get("unmatched", [])),
            "ambiguous": len(mapping.get("ambiguous", [])),
        },
        "class_score_ge7": class_metrics,
        "PH04_ge7": m7,
        "PH04_ge8": m8,
        "PH04_ge9": m9,
        "decision": verdict,
    }
    (out / "decision.json").write_text(json.dumps(decision_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
