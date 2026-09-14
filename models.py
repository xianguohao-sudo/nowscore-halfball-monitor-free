from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class BookmakerOdds:
    company: str
    ah_open_home: Optional[float] = None
    ah_open_line: Optional[str] = None
    ah_open_away: Optional[float] = None
    ah_now_home: Optional[float] = None
    ah_now_line: Optional[str] = None
    ah_now_away: Optional[float] = None
    x12_open_home: Optional[float] = None
    x12_open_draw: Optional[float] = None
    x12_open_away: Optional[float] = None
    x12_now_home: Optional[float] = None
    x12_now_draw: Optional[float] = None
    x12_now_away: Optional[float] = None

@dataclass
class MatchOdds:
    match_id: str
    league: str
    kickoff: str
    home: str
    away: str
    url: str
    rows: List[BookmakerOdds] = field(default_factory=list)
