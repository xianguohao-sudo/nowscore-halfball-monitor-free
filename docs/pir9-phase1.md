# PIR-9 V1.0: first-stage research module (experimental)

This folder is **independent** of `scan.py`, `fingerprint.py`, existing backtests, the ServerChan notifier, and all GitHub Actions workflows. It neither scrapes websites nor publishes betting recommendations. It rejects unreliable historical snapshots instead of silently accepting in-play data.

## Run

From the repository root with Python 3.9+:

```shell
python -m unittest discover -s tests -p 'test_phase1.py' -v
python -m pir9.phase1 --input data/pir9_normalized.jsonl --output data/pir9_report.json
```

No `data/pir9_normalized.jsonl` is shipped. **No real 2026-08-01 to 2026-10-19 historical dataset has been collected as part of this change**, and no 500-game success rate is claimed. The local demonstration tests use synthetic fixtures and never count as real games.

## One JSONL record per match

```json
{"match_id":"real-id","kickoff":"2026-10-18T10:30:00+08:00","league":"example","home":"Home","away":"Away","home_goals":1,"away_goals":0,"companies":[{"company":"bookmaker-id","source_url":"https://source.example/original","source_status":"documented_pregame","open_at":"2026-10-17T10:30:00+08:00","close_at":"2026-10-18T10:20:00+08:00","ah_open_line":-0.5,"ah_close_line":-0.5,"ah_open_home":1.93,"ah_open_away":1.93,"ah_close_home":1.9,"ah_close_away":1.95,"x12_open_home":1.95,"x12_open_draw":3.5,"x12_open_away":4.0,"x12_close_home":1.9,"x12_close_draw":3.55,"x12_close_away":4.1}]}
```

Provide **4 or more distinct, valid company records** in each match. Example shows *one* company to explain fields, so it will be **rejected** if executed literally.

- Timestamps must include UTC offset. `close_at < kickoff`; a later scrape can provide an archive only if source-backed **actual** pregame change times exist. A current odds page labeled `即时` without a change timestamp is not enough.
- `source_status=documented_pregame` is an explicit assertion by the data adapter; the importer must verify underlying source status and timestamps. This gate does **not** independently authenticate that claim or detect fabricated values.
- Handicap is the home team's **signed** line: `-0.5` means home gives half a goal, `+0.25` means home receives a quarter goal. If a historical Chinese line cannot safely establish direction, reject the row rather than assume home is giving.
- Prices must be **decimal prices including stake**, not Hong Kong (`0.80`) or Indonesian style prices. Conversion is the adapter's responsibility, not a guess.
- Results must be full-time 90-minute scores (including stoppage time) without overtime or shootouts. No results => no settlement.
- The rules inspect open and close snapshots but still need league baseline, independent probability estimation, market calibration and sample-size thresholds before deployment.

## Candidate interpretation and limits

`R1` = halfball fake-strong-home (>=4 of 6, with true-strength veto); `R2` = away -0.25 to PK; `R3` = PK away favorite reversal; `R4` = Europe/Asian divergence (diagnostic, no pick); `R5` = deep handicap vs pre-match expected margin; `R6` = true-strong synchronized move. R2/R3 are **simplified preliminary flags**, not full replacements for original 5/6 or 6/8 historical rule sets. They should be refined in a separate review after rigorous replay of the legacy algorithms.

The output's `decision` is always `SKIP` until independent estimated fair probabilities, value checks and holdout testing are implemented. Descriptive rule metrics are in-sample **candidate settlements**, not validated returns. A match can trigger multiple candidate rules: do not sum them as independent match observations. The module intentionally does not wire itself to the live notifier.

## Next ingestion steps

1. Generate a match manifest for 2026-08-01 to 2026-10-19 containing competition, kickoff, teams, game IDs and official results (90 minutes).
2. Use existing `history_collector` only as a **source**, retaining `change_time`, `status`, `source_url`, and company IDs. Normalize signed handicap and decimal odds with explicit evidence; supply actual timezone. Reject status `滚`, unparseable changes, or snapshot times >= kickoff.
3. Produce per-match audit rejection summary; target 500+ **accepted**, independent matches, not 500 raw IDs. Check missingness by league and by source.
4. Freeze rules; evaluate held-out later matches with pre-declared thresholds, baseline probabilities, mean returns and confidence intervals. If a rule lacks evidence, keep it in observation-only mode.

