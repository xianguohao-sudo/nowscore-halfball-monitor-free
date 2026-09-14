"""Verify that the historical parser excludes every in-play ('滚') quote."""
from backtest.pregame_nowscore import fetch_match_pregame


def main():
    match = fetch_match_pregame("3000458", companies=4)
    print(match.home, "vs", match.away, "kickoff", match.kickoff)
    for row in match.rows:
        print(
            row.company,
            "AH", row.ah_open_home, row.ah_open_line, row.ah_open_away,
            "->", row.ah_now_home, row.ah_now_line, row.ah_now_away,
            "1X2", row.x12_open_home, row.x12_open_draw, row.x12_open_away,
            "->", row.x12_now_home, row.x12_now_draw, row.x12_now_away,
        )
        assert row.x12_now_home > 1.10
        assert row.x12_now_draw > 1.10
        assert row.x12_now_away > 1.10
        assert row.ah_now_home < 2.0 and row.ah_now_away < 2.0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
