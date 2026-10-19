"""PIR-9 phase 1: isolated pre-match audit, candidate rules and backtesting.

No network, no notifications, no existing model imports. Python 3.9+ stdlib.
Expected input: one normalized match JSON object per line, see README.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

RULES = ('R1', 'R2', 'R3', 'R4', 'R5', 'R6')
PICKS = ('home', 'draw', 'away', 'home_ah', 'away_ah')
REQUIRED_1X2 = ('x12_open_home', 'x12_open_draw', 'x12_open_away',
                'x12_close_home', 'x12_close_draw', 'x12_close_away')


def iso(value: str) -> datetime:
    """Require an explicit UTC offset, including Z. Naive historical times fail closed."""
    if not isinstance(value, str):
        raise ValueError('timestamp missing')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('timestamp timezone missing')
    return dt.astimezone(timezone.utc)


def real(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError('boolean not numeric')
    n = float(value)
    if not math.isfinite(n) or not low <= n <= high:
        raise ValueError(f'numeric value out of range {value}')
    return n


def audit(match: dict) -> list[str]:
    """Fail closed: 4 distinct companies with complete, provably pre-kickoff odds."""
    errors = []
    try:
        kickoff = iso(match['kickoff'])
    except (KeyError, ValueError, TypeError) as exc:
        return [f'kickoff: {exc}']
    if not str(match.get('match_id', '')).strip():
        errors.append('match_id missing')
    companies = match.get('companies', [])
    if not isinstance(companies, list):
        return ['companies must be list']
    if len(companies) < 4:
        errors.append('fewer than four bookmakers')
    seen = set()
    for i, row in enumerate(companies):
        cid = str(row.get('company', '')).strip() if isinstance(row, dict) else ''
        tag = f'company[{i}]'
        if not cid or cid in seen:
            errors.append(f'{tag}: missing or repeated bookmaker')
        seen.add(cid)
        try:
            opening, closing = iso(row['open_at']), iso(row['close_at'])
            if not opening <= closing < kickoff:
                errors.append(f'{tag}: non-pregame or reversed timestamps')
            # A later archived download is acceptable only when the actual change
            # timestamps are documented. Reject scraped "now" as evidence.
            if row.get('source_status') != 'documented_pregame':
                errors.append(f'{tag}: unverified pregame source')
            if not str(row.get('source_url', '')).startswith(('https://', 'http://')):
                errors.append(f'{tag}: missing source_url')
            for key in REQUIRED_1X2:
                real(row[key], 1.01, 1000.0)
            for key in ('ah_open_home', 'ah_open_away', 'ah_close_home', 'ah_close_away'):
                real(row[key], 1.01, 100.0)  # normalized decimal (not Hong Kong odds)
            for key in ('ah_open_line', 'ah_close_line'):
                line = real(row[key], -5, 5)
                if abs(line * 4 - round(line * 4)) > 1e-8:
                    errors.append(f'{tag}: unsupported handicap increment')
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            errors.append(f'{tag}: {exc}')
    if match.get('home_goals') is not None or match.get('away_goals') is not None:
        try:
            for key in ('home_goals', 'away_goals'):
                value = real(match[key], 0, 30)
                if not value.is_integer():
                    errors.append(f'{key} not integer')
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(f'score: {exc}')
    return errors


def consensus(rows: list[dict], key: str, target: float, threshold: float = .6) -> bool:
    return sum(abs(float(r[key]) - target) < 1e-8 for r in rows) >= max(3, math.ceil(len(rows) * threshold))


def analyze(match: dict) -> dict:
    errors = audit(match)
    if errors:
        return {'match_id': match.get('match_id'), 'quality': 'REJECT',
                'errors': errors, 'candidates': [], 'decision': 'SKIP'}
    rows = match['companies']
    med = lambda key: median(float(r[key]) for r in rows)
    h0, h1 = med('x12_open_home'), med('x12_close_home')
    a0, a1 = med('x12_open_away'), med('x12_close_away')
    lo, lc = med('ah_open_line'), med('ah_close_line')
    candidates = []
    def add(rule, direction, reasons):
        candidates.append({'rule': rule, 'direction': direction, 'reasons': reasons})
    # Home handicap uses a signed convention: negative = home gives goals.
    # R1: legacy fingerprint requires 4/6 conditions AND a hard -0.5 condition.
    checks = [consensus(rows, 'ah_close_line', -.5),
              1.90 <= h1 <= 2.10,
              1.70 <= med('ah_close_away') <= 1.85,
              h1 - h0 <= .03,
              a1 - a0 >= -.02,
              not consensus(rows, 'ah_open_line', -.75) and not consensus(rows, 'ah_close_line', -.75)]
    if checks[0] and sum(checks) >= 4:
        # Veto when the market truly strengthens the home side from quarter to half.
        if not (consensus(rows, 'ah_open_line', -.25) and h1 - h0 <= -.15 and a1 - a0 >= .20):
            add('R1', 'away_ah', [f'{sum(checks)}/6 fingerprint'])
    # R2: clear consensus away -0.25 to PK; never treat neutral home as automatic win.
    if consensus(rows, 'ah_open_line', .25) and consensus(rows, 'ah_close_line', 0) and h1 < h0 and a1 > a0:
        add('R2', 'home_ah', ['away -0.25 -> PK', 'home odds shorten'])
    # R3: opening PK with away favored, finishing PK with home favored.
    if consensus(rows, 'ah_open_line', 0) and consensus(rows, 'ah_close_line', 0) and a0 < h0 and h1 < a1 and h1 < h0:
        add('R3', 'home_ah', ['PK away favorite -> home favorite'])
    # R4: Asian line moves against the side whose 1x2 shortens.
    if (h1 < h0 - .08 and lc > lo + .01) or (a1 < a0 - .08 and lc < lo - .01):
        add('R4', 'SKIP', ['European/Asian divergence; diagnostic only'])
    # R5: only with independently produced, time-stamped expected goal margin.
    fair = match.get('pregame_expected_goal_margin')
    fair_at = match.get('expected_margin_asof')
    if fair is not None and fair_at is not None:
        try:
            if iso(fair_at) < iso(match['kickoff']) and lc <= -1 and real(fair, -5, 5) < abs(lc) - .35:
                add('R5', 'away_ah', ['deep handicap beyond model margin'])
        except (ValueError, TypeError):
            pass
    # R6: true home favorite needs line strengthening + odds strengthening.
    if lc <= lo - .24 and h1 < h0 - .08 and a1 > a0 + .08 and lc <= -.5:
        add('R6', 'home_ah', ['Asian/European line strengthen together'])
    directions = {c['direction'] for c in candidates if c['direction'] != 'SKIP'}
    # Phase 1 is a retrospective candidate detector, not a calibrated predictor.
    # A conflict, no candidate, or unmeasured price edge => always SKIP.
    return {'match_id': match['match_id'], 'quality': 'PASS', 'errors': [],
            'candidates': candidates, 'direction_candidate': next(iter(directions)) if len(directions) == 1 else None,
            'decision': 'SKIP', 'reason': 'unvalidated_price_edge' if len(directions) == 1 else 'no_unique_direction'}


def ah_return(goal_diff: int, home_handicap: float, side: str, decimal_price: float) -> float:
    """Net return per one unit stake; quarter handicap is two half-stakes."""
    price = real(decimal_price, 1.01, 100)
    line = real(home_handicap, -5, 5)
    if side not in ('home_ah', 'away_ah'):
        raise ValueError('invalid Asian-handicap side')
    steps = round(line * 4)
    offsets = [line] if steps % 2 == 0 else [(steps - 1) / 4, (steps + 1) / 4]
    results = []
    for offset in offsets:
        margin = (goal_diff + offset) * (1 if side == 'home_ah' else -1)
        results.append((price - 1) if margin > 1e-8 else (-1 if margin < -1e-8 else 0.0))
    return sum(results) / len(results)


def win_return(match: dict, direction: str) -> float | None:
    if match.get('home_goals') is None or match.get('away_goals') is None:
        return None
    rows = match['companies']
    diff = int(match['home_goals']) - int(match['away_goals'])
    if direction in ('home_ah', 'away_ah'):
        price = median(real(r['ah_close_home' if direction == 'home_ah' else 'ah_close_away'], 1.01, 100) for r in rows)
        line = median(real(r['ah_close_line'], -5, 5) for r in rows)
        return ah_return(diff, line, direction, price)
    if direction in ('home', 'draw', 'away'):
        actual = 'home' if diff > 0 else ('away' if diff < 0 else 'draw')
        price = median(real(r['x12_close_' + direction], 1.01, 1000) for r in rows)
        return price - 1 if direction == actual else -1.0
    return None


def backtest(matches: list[dict]) -> dict:
    """Descriptive candidate settlements only; never report them as live strategy ROI."""
    unique, results, errors = set(), defaultdict(list), Counter()
    for m in matches:
        mid = str(m.get('match_id', ''))
        if mid in unique:
            errors['duplicate_match_id'] += 1
            continue
        unique.add(mid)
        decision = analyze(m)
        if decision['quality'] != 'PASS':
            errors['quality_reject'] += 1
            continue
        for c in decision['candidates']:
            if c['direction'] == 'SKIP':
                errors['diagnostic_only'] += 1
                continue
            payout = win_return(m, c['direction'])
            if payout is None:
                errors['missing_final_score'] += 1
                continue
            results[c['rule']].append(payout)
    rows = {}
    for rule in RULES:
        pay = results[rule]
        count = len(pay)
        rows[rule] = {'settled_matches': count,
                      'positive_return_rate': round(sum(v > 0 for v in pay) / count, 4) if count else None,
                      'flat_stake_roi': round(sum(pay) / count, 4) if count else None,
                      'warning': 'IN_SAMPLE_ONLY_not_validated'}
    return {'input_matches': len(matches), 'unique_ids': len(unique), 'rejections': dict(errors),
            'rules': rows, 'note': 'Candidate diagnostics only; not validated profitable or deployable.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='PIR-9 phase-1 isolated audit and retrospective rule tester')
    parser.add_argument('--input', type=Path, required=True, help='normalized JSONL, one match per line')
    parser.add_argument('--output', type=Path, required=True, help='JSON summary path')
    args = parser.parse_args(argv)
    records = []
    for n, line in enumerate(args.input.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f'invalid JSONL at line {n}: {exc}')
    report = backtest(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

