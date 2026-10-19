import unittest
from datetime import datetime, timedelta, timezone
from pir9.phase1 import audit, analyze, ah_return, backtest


def fixture(line=-0.5):
    kickoff = datetime(2026, 10, 18, 10, 30, tzinfo=timezone.utc)
    companies = []
    for n in range(4):
        companies.append({
            'company': f'book-{n}', 'source_url': f'https://example.org/odds/{n}',
            'source_status': 'documented_pregame',
            'open_at': (kickoff - timedelta(days=1)).isoformat(),
            'close_at': (kickoff - timedelta(minutes=10)).isoformat(),
            'ah_open_line': line, 'ah_close_line': line,
            'ah_open_home': 1.9, 'ah_open_away': 1.8,
            'ah_close_home': 1.9, 'ah_close_away': 1.8,
            'x12_open_home': 2.05, 'x12_open_draw': 3.4, 'x12_open_away': 3.9,
            'x12_close_home': 1.98, 'x12_close_draw': 3.4, 'x12_close_away': 4.0,
        })
    return {'match_id': 'example-only', 'kickoff': kickoff.isoformat(),
            'home_goals': 1, 'away_goals': 1, 'companies': companies}


class Phase1Test(unittest.TestCase):
    def test_valid_and_never_auto_deploy(self):
        m = fixture()
        self.assertEqual(audit(m), [])
        result = analyze(m)
        self.assertEqual(result['quality'], 'PASS')
        self.assertIn('R1', [c['rule'] for c in result['candidates']])
        self.assertEqual(result['decision'], 'SKIP')

    def test_kickoff_and_company_time_leak(self):
        m = fixture()
        m['companies'][0]['close_at'] = m['kickoff']
        self.assertEqual(analyze(m)['quality'], 'REJECT')

    def test_missing_timezone_or_source(self):
        m = fixture()
        m['companies'][0]['close_at'] = '2026-10-18 10:20'
        self.assertEqual(analyze(m)['quality'], 'REJECT')
        m = fixture()
        m['companies'][1]['source_status'] = 'rolling'
        self.assertEqual(analyze(m)['quality'], 'REJECT')

    def test_incomplete_sample(self):
        m = fixture()
        m['companies'].pop()
        self.assertEqual(analyze(m)['quality'], 'REJECT')

    def test_quarter_settlement(self):
        self.assertAlmostEqual(ah_return(0, -.25, 'home_ah', 1.9), -.5)
        self.assertAlmostEqual(ah_return(0, .25, 'home_ah', 1.9), .45)
        self.assertAlmostEqual(ah_return(1, -.75, 'home_ah', 1.9), .45)
        self.assertAlmostEqual(ah_return(1, -1, 'home_ah', 1.9), 0)
        self.assertAlmostEqual(ah_return(0, -.25, 'away_ah', 1.8), .4)

    def test_zero_sample_does_not_fake_success(self):
        report = backtest([])
        self.assertEqual(report['input_matches'], 0)
        self.assertIsNone(report['rules']['R1']['flat_stake_roi'])

    def test_duplicate_match_fails_to_expand_sample(self):
        m = fixture()
        report = backtest([m, m])
        self.assertEqual(report['unique_ids'], 1)
        self.assertEqual(report['rejections']['duplicate_match_id'], 1)


if __name__ == '__main__':
    unittest.main()

