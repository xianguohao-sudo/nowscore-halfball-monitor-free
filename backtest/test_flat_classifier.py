import unittest

from flat_classifier import evaluate_flat, line_value
from models import BookmakerOdds, MatchOdds


def make_match(open_line, now_line, direction="home", companies=8):
    rows = []
    for index in range(companies):
        home_supported = direction == "home"
        rows.append(BookmakerOdds(
            company=f"36-{index}",
            ah_open_home=0.96,
            ah_open_line=open_line,
            ah_open_away=0.94,
            ah_now_home=0.86 if home_supported else 1.02,
            ah_now_line=now_line,
            ah_now_away=1.02 if home_supported else 0.86,
            x12_open_home=2.45,
            x12_open_draw=3.10,
            x12_open_away=2.65,
            x12_now_home=2.30 if home_supported else 2.55,
            x12_now_draw=3.10,
            x12_now_away=2.75 if home_supported else 2.50,
        ))
    return MatchOdds("1", "test", "2026-01-01 12:00", "H", "A", "", rows)


class FlatClassifierTest(unittest.TestCase):
    def test_line_normalization(self):
        self.assertEqual(line_value("平手"), 0.0)
        self.assertEqual(line_value("平/半"), -0.25)
        self.assertEqual(line_value("受让平/半"), 0.25)

    def test_six_paths(self):
        cases = [
            ("平手", "平手", "neutral", "F1"),
            ("平手", "平手", "home", "F2"),
            ("平手", "平手", "away", "F3"),
            ("平手", "平/半", "home", "F4"),
            ("平/半", "平手", "away", "F5"),
            ("平/半", "受让平/半", "away", "F6"),
        ]
        for opening, latest, direction, expected in cases:
            with self.subTest(expected=expected):
                if direction == "neutral":
                    match = make_match(opening, latest, "home")
                    for row in match.rows:
                        row.ah_now_home = row.ah_now_away = 0.94
                        row.x12_now_home = row.x12_open_home
                        row.x12_now_away = row.x12_open_away
                else:
                    match = make_match(opening, latest, direction)
                result = evaluate_flat(match)
                self.assertEqual(result["class_id"], expected)


if __name__ == "__main__":
    unittest.main()
