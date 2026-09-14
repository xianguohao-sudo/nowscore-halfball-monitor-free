"""Print raw Nowscore odds-table headers and parsed columns for mapping audit."""
import requests
from bs4 import BeautifulSoup

from nowscore import HEADERS, fetch_match


def main():
    match_id = "3000458"
    url = f"https://live.nowscore.com/odds/match/{match_id}.htm"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "lxml")
    print("URL", url, "TITLE", soup.title.get_text(" ", strip=True) if soup.title else "")
    printed = 0
    for index, tr in enumerate(soup.find_all("tr")):
        cells = [" ".join(td.get_text(" ", strip=True).split()) for td in tr.find_all(["td", "th"])]
        if cells:
            print("RAW_ROW", index, repr(cells))
            printed += 1
        if printed >= 25:
            break
    match = fetch_match(match_id)
    print("PARSED", match.home, "vs", match.away, "rows", len(match.rows))
    for row in match.rows[:8]:
        print("PARSED_ROW", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
