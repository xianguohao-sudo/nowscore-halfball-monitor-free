import unittest

from models import BookmakerOdds, MatchOdds
from quarter_classifier import evaluate_quarter, line_value


def row(
    company,
    open_line,
    now_line,
    oh=2.30, od=3.20, oa=3.10,
    nh=2.20, nd=3.20, na=3.30,
    ah_open_home=0.95, ah_open_away=0.95,
    ah_now_home=0.88, ah_now_away=1.00,
    ou=2.5,
):
    return BookmakerOdds(
        company=company,
        ah_open_home=ah_open_home, ah_open_line=open_line, ah_open_away=ah_open_away,
        ah_now_home=ah_now_home, ah_now_line=now_line, ah_now_away=ah_now_away,
        x12_open_home=oh, x12_open_draw=od, x12_open_away=oa,
        x12_now_home=nh, x12_now_draw=nd, x12_now_away=na,
        ou_now_line_value=ou,
    )


def match(rows):
    return MatchOdds("1", "Test", "2026-01-01 12:00", "Home", "Away", "", rows)


class QuarterClassifierTest(unittest.TestCase):
    def test_line_parser(self):
        self.assertEqual(line_value("平手"), 0.0)
        self.assertEqual(line_value("平/半"), -0.25)
        self.assertEqual(line_value("受平/半"), 0.25)
        self.assertEqual(line_value("半球"), -0.5)
        self.assertEqual(line_value("受半球"), 0.5)

    def test_ph01_flat_to_quarter(self):
        rows = [row(str(i), "平手", "平/半") for i in range(4)]
        result = evaluate_quarter(match(rows))
        self.assertEqual(result["class_id"], "PH01")
        self.assertEqual(result["direction"], "home")

    def test_ph02_stable_quarter(self):
        rows = [
            row(str(i), "平/半", "平/半", oh=2.20, nh=2.16, od=3.30, nd=3.25, oa=3.35, na=3.40)
            for i in range(4)
        ]
        result = evaluate_quarter(match(rows))
        self.assertEqual(result["class_id"], "PH02")

    def test_ph03_strong_shallow(self):
        rows = [
            row(str(i), "平/半", "平/半", oh=1.98, nh=1.96, od=3.45, nd=3.40, oa=3.90, na=3.95)
            for i in range(4)
        ]
        result = evaluate_quarter(match(rows))
        self.assertEqual(result["class_id"], "PH03")
        self.assertEqual(result["direction"], "away")

    def test_ph04_half_retreat(self):
        rows = [
            row(str(i), "半球", "平/半", oh=1.90, nh=2.05, od=3.40, nd=3.25, oa=4.10, na=3.70)
            for i in range(4)
        ]
        result = evaluate_quarter(match(rows))
        self.assertEqual(result["class_id"], "PH04")
        self.assertEqual(result["direction"], "away")

    def test_ph05_euro_asian_divergence(self):
        rows = [
            row(str(i), "平/半", "平/半", oh=2.18, nh=2.30, od=3.30, nd=3.25, oa=3.25, na=3.12)
            for i in range(4)
        ]
        result = evaluate_quarter(match(rows))
        self.assertEqual(result["class_id"], "PH05")

    def test_ph06_draw_low_total(self):
        rows = [
            row(str(i), "平/半", "平/半", oh=2.24, nh=2.24, od=3.05, nd=2.98, oa=3.30, na=3.32, ou=2.25)
            for i in range(4)
        ]
        result = evaluate_quarter(match(rows))
        self.assertEqual(result["class_id"], "PH06")

    def test_away_favorite_supported(self):
        rows = [
            row(
                str(i), "受平/半", "受平/半",
                oh=3.40, nh=3.45, od=3.30, nd=3.28, oa=2.18, na=2.12,
                ah_open_home=0.94, ah_open_away=0.94,
                ah_now_home=1.00, ah_now_away=0.88,
            )
            for i in range(4)
        ]
        result = evaluate_quarter(match(rows))
        self.assertTrue(result["classified"])
        self.assertEqual(result["favorite"], "away")
        self.assertEqual(result["direction"], "away")


if __name__ == "__main__":
    unittest.main()
