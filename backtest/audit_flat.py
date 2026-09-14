"""Inspect Nowscore detail/history links needed for pre-kickoff closing odds."""
import re
import requests
from bs4 import BeautifulSoup

from nowscore import HEADERS


def main():
    match_id = "3000458"
    url = f"https://live.nowscore.com/odds/match/{match_id}.htm"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "lxml")
    for index, tr in enumerate(soup.find_all("tr")[:18]):
        cells = [" ".join(td.get_text(" ", strip=True).split()) for td in tr.find_all(["td", "th"])]
        if not cells:
            continue
        links = [
            {
                "text": a.get_text(" ", strip=True),
                "href": a.get("href"),
                "onclick": a.get("onclick"),
            }
            for a in tr.find_all("a")
        ]
        print("ROW", index, repr(cells[:13]))
        print("LINKS", index, repr(links))
    html = response.text
    for pattern in ("AsianOdds", "OddsHistory", "changeDetail", "companyID", "handicap"):
        matches = re.findall(r".{0,120}" + pattern + r".{0,220}", html, re.I)
        print("PATTERN", pattern, repr(matches[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
