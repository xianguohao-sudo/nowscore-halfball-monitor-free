"""Audit whether historical 'latest' odds rows stop before kickoff."""
import re
import requests
from bs4 import BeautifulSoup

from nowscore import BASE, HEADERS, fetch_match


MATCH_IDS = ["2908783", "2997410", "3001172"]
COMPANY_IDS = [3, 8, 12, 18]


def cells(row):
    return [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.find_all(["th", "td"])]


def main():
    for match_id in MATCH_IDS:
        match = fetch_match(match_id)
        print(f"AUDIT_MATCH id={match_id} kickoff={match.kickoff} teams={match.home} vs {match.away}")
        for company_id in COMPANY_IDS:
            url = f"{BASE}/odds/3in1Odds.aspx?companyid={company_id}&id={match_id}"
            try:
                response = requests.get(url, headers=HEADERS, timeout=25)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or "utf-8"
                soup = BeautifulSoup(response.text, "lxml")
                rows = [cells(row) for row in soup.find_all("tr")]
                timed = [
                    row for row in rows
                    if any(re.search(r"(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}|\d{1,2}:\d{2})", value) for value in row)
                ]
                print(
                    f"AUDIT_COMPANY match={match_id} company={company_id} "
                    f"title={soup.title.get_text(' ', strip=True) if soup.title else ''} timed_rows={len(timed)}"
                )
                for row in timed[-12:]:
                    print(f"AUDIT_ROW match={match_id} company={company_id} row={row}")
            except Exception as exc:
                print(f"AUDIT_ERROR match={match_id} company={company_id} error={exc!r}")


if __name__ == "__main__":
    main()
