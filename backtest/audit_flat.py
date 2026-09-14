"""Inspect analysis-page links for older completed match expansion."""
import re
import requests
from bs4 import BeautifulSoup

from nowscore import HEADERS


def main():
    url = "https://live.nowscore.com/analysis/3000458cn.html"
    response = requests.get(url, headers=HEADERS, timeout=30)
    print("STATUS", response.status_code, "URL", response.url)
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "lxml")
    print("TITLE", soup.title.get_text(" ", strip=True) if soup.title else "")
    found = {}
    for a in soup.find_all("a"):
        href = a.get("href", "")
        match = re.search(r"(?:analysis/|MatchDetail/|id=)(\d+)", href, re.I)
        if match:
            found[match.group(1)] = (a.get_text(" ", strip=True), href)
    print("MATCH_LINKS", len(found), repr(list(found.items())[:80]))
    for index, tr in enumerate(soup.find_all("tr")[:80]):
        cells = [" ".join(td.get_text(" ", strip=True).split()) for td in tr.find_all(["td", "th"])]
        if any(re.search(r"\b\d{1,2}-\d{1,2}\b", cell) for cell in cells):
            print("SCORE_ROW", index, repr(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
