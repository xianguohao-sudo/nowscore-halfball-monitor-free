"""Inspect bookmaker detail history for a valid pre-kickoff cutoff."""
import re
import requests
from bs4 import BeautifulSoup

from nowscore import HEADERS


def main():
    urls = [
        "https://live.nowscore.com/odds/3in1Odds.aspx?companyid=8&id=3000458",
        "https://live.nowscore.com/odds/3in1Odds.aspx?companyid=3&id=3000458",
    ]
    for url in urls:
        response = requests.get(url, headers=HEADERS, timeout=30)
        print("URL", url, "STATUS", response.status_code)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "lxml")
        print("TITLE", soup.title.get_text(" ", strip=True) if soup.title else "")
        for index, tr in enumerate(soup.find_all("tr")):
            cells = [" ".join(td.get_text(" ", strip=True).split()) for td in tr.find_all(["td", "th"])]
            if cells:
                print("DETAIL_ROW", index, repr(cells))
        for script in soup.find_all("script"):
            text = script.get_text("\n", strip=True)
            if re.search(r"2026|odds|game|data|change", text, re.I):
                print("SCRIPT", repr(text[:4000]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
