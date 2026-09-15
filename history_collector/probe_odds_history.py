import argparse
import csv
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
URL = "https://m.nowscore.com/Analy/JcOddsDetail?oddsType={odds_type}&scheid={match_id}"
TIME_RE = re.compile(r"^(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$")
NUM_RE = re.compile(r"^\d+(?:\.\d+)?$")


def f(s):
    try:
        return float(s)
    except Exception:
        return None


def parse_change_time(raw, kickoff):
    m = TIME_RE.match(raw.strip())
    if not m or not kickoff:
        return None
    mo, day, hh, mm = map(int, m.groups())
    # 页面不带年份：优先使用开赛年份；跨年时选择离 kickoff 最近的候选。
    candidates = []
    for year in (kickoff.year - 1, kickoff.year, kickoff.year + 1):
        try:
            candidates.append(datetime(year, mo, day, hh, mm))
        except ValueError:
            pass
    return min(candidates, key=lambda x: abs((kickoff - x).total_seconds())) if candidates else None


def fetch_history(match_id, odds_type=""):
    url = URL.format(odds_type=odds_type, match_id=match_id)
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [re.sub(r"\s+", " ", x.get_text(" ", strip=True)).strip() for x in tr.find_all(["td", "th"])]
        if not cells:
            continue
        time_idx = next((i for i, x in enumerate(cells) if TIME_RE.match(x)), None)
        if time_idx is None:
            continue
        nums = [(i, f(x)) for i, x in enumerate(cells[:time_idx]) if NUM_RE.match(x)]
        if len(nums) < 3:
            continue
        vals = [v for _, v in nums[-3:]]
        rows.append({
            "match_id": str(match_id), "odds_type": odds_type or "default",
            "raw_time": cells[time_idx], "v1": vals[0], "v2": vals[1], "v3": vals[2],
            "raw_cells": " | ".join(cells), "source_url": url,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match-id", default="2789129")
    ap.add_argument("--kickoff", default="2025-08-15 20:00", help="YYYY-MM-DD HH:MM，先用于穿透验证")
    ap.add_argument("--out", default="data/history/odds_history_probe.csv")
    args = ap.parse_args()
    kickoff = datetime.strptime(args.kickoff, "%Y-%m-%d %H:%M")

    # 先验证公开的变化记录入口。不同 oddsType 的市场含义后续以页面标签/响应继续确认，避免猜字段。
    rows = fetch_history(args.match_id, "")
    for x in rows:
        dt = parse_change_time(x["raw_time"], kickoff)
        x["change_time"] = dt.strftime("%Y-%m-%d %H:%M") if dt else ""
        x["minutes_to_kickoff"] = int((kickoff - dt).total_seconds() // 60) if dt else None
        x["is_pregame"] = 1 if dt and dt < kickoff else 0

    pre = [x for x in rows if x["is_pregame"]]
    pre.sort(key=lambda x: x["change_time"])
    close = pre[-1] if pre else None

    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["match_id","odds_type","raw_time","change_time","minutes_to_kickoff","is_pregame","v1","v2","v3","raw_cells","source_url"]
    with out.open("w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=fields); w.writeheader(); w.writerows(rows)

    print("=" * 70)
    print("赔率变化时间序列穿透测试")
    print("比赛ID:", args.match_id)
    print("人工指定开赛时间:", kickoff.strftime("%Y-%m-%d %H:%M"))
    print("变化记录:", len(rows))
    print("赛前记录:", len(pre))
    if close:
        print("赛前最后记录:", close["change_time"], "距开赛", close["minutes_to_kickoff"], "分钟", close["v1"], close["v2"], close["v3"])
    else:
        print("赛前最后记录: 未找到")
    print("注意: v1/v2/v3 市场语义尚未验证，不进入正式回测。")
    print("输出:", out)
    print("=" * 70)


if __name__ == "__main__":
    main()
