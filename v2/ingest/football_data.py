"""football-data.co.uk ingest.

Two corrections to v1, both verified against the live CSV headers:

  * v1's column map omitted HF/AF (fouls committed). Fouls are the causal
    mechanism behind cards and are present for all four leagues, retroactively,
    for free. They are the highest-value feature we were not using.
  * v1 mapped PSH/PSD/PSA -> pinnacle_h/d/a. Those are Pinnacle *opening*
    odds. PSCH/PSCD/PSCA are the *closing* line. Every "sharp reference"
    probability in v1 was therefore built on opening prices. We ingest both
    and keep them distinct; the open->close drift is itself informative.

Unlike v1's entry point, this module downloads. It never reads a gitignored
local file, and a season that yields zero rows is an error, not a no-op.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterator, List, Optional

from v2.lib.config import LEAGUES
from v2.lib.http import get

BASE_URL = "https://www.football-data.co.uk/mmz4281"

# csv column -> our field. Anything not listed is deliberately dropped.
COLUMN_MAP = {
    "Date": "date", "Time": "time", "Referee": "referee",
    "HomeTeam": "home_team", "AwayTeam": "away_team",
    "FTHG": "home_goals", "FTAG": "away_goals", "FTR": "result",
    "HTHG": "ht_home_goals", "HTAG": "ht_away_goals", "HTR": "ht_result",
    "HS": "home_shots", "AS": "away_shots",
    "HST": "home_shots_ot", "AST": "away_shots_ot",
    "HF": "home_fouls", "AF": "away_fouls",          # NEW - absent from v1
    "HC": "home_corners", "AC": "away_corners",
    "HY": "home_yellow", "AY": "away_yellow",
    "HR": "home_red", "AR": "away_red",
    # Pinnacle closing (the sharp reference) ...
    "PSCH": "pinnacle_close_h", "PSCD": "pinnacle_close_d", "PSCA": "pinnacle_close_a",
    # ... and opening, kept separately rather than mislabelled as closing.
    "PSH": "pinnacle_open_h", "PSD": "pinnacle_open_d", "PSA": "pinnacle_open_a",
    "B365H": "b365_h", "B365D": "b365_d", "B365A": "b365_a",
    "AvgH": "avg_h", "AvgD": "avg_d", "AvgA": "avg_a",
    # ── every book we can get, opening and closing ────────────────────────
    # The archive carries a whole market, not just Pinnacle. Closing prices
    # from a soft book (Bet365) against Pinnacle's closing fair value is a
    # line-shopping test on ~8,000 matches for free; the opening prices make
    # the stronger version possible - early soft price vs the eventual close.
    "B365CH": "b365_close_h", "B365CD": "b365_close_d", "B365CA": "b365_close_a",
    "MaxCH": "max_close_h", "MaxCD": "max_close_d", "MaxCA": "max_close_a",
    "AvgCH": "avg_close_h", "AvgCD": "avg_close_d", "AvgCA": "avg_close_a",
    "BFECH": "bfe_close_h", "BFECD": "bfe_close_d", "BFECA": "bfe_close_a",
    "BWCH": "bw_close_h", "BWCD": "bw_close_d", "BWCA": "bw_close_a",
    "MaxH": "max_open_h", "MaxD": "max_open_d", "MaxA": "max_open_a",
    # over/under 2.5 goals
    "B365C>2.5": "b365_close_over25", "B365C<2.5": "b365_close_under25",
    "MaxC>2.5": "max_close_over25", "MaxC<2.5": "max_close_under25",
    "B365>2.5": "b365_open_over25", "B365<2.5": "b365_open_under25",
    "P>2.5": "pinnacle_open_over25", "P<2.5": "pinnacle_open_under25",
    "Max>2.5": "max_open_over25", "Max<2.5": "max_open_under25",
    # asian handicap - PCAHH/PCAHA is Pinnacle's CLOSING handicap price
    "AHCh": "ah_close_line",
    "PCAHH": "pinnacle_close_ah_home", "PCAHA": "pinnacle_close_ah_away",
    "B365CAHH": "b365_close_ah_home", "B365CAHA": "b365_close_ah_away",
    "MaxCAHH": "max_close_ah_home", "MaxCAHA": "max_close_ah_away",
    "AvgCAHH": "avg_close_ah_home", "AvgCAHA": "avg_close_ah_away",
    "PAHH": "pinnacle_open_ah_home", "PAHA": "pinnacle_open_ah_away",
    "B365AHH": "b365_open_ah_home", "B365AHA": "b365_open_ah_away",
    # Closing over/under. "Avg>2.5"/"P>2.5" are the OPENING prices; the C
    # variants are the close. Ingesting the opening one and calling it the
    # market benchmark makes a model look prescient, because closing lines are
    # far sharper than openings - it is the same trap as PSH vs PSCH above.
    "PC>2.5": "pinnacle_close_over25", "PC<2.5": "pinnacle_close_under25",
    "AvgC>2.5": "avg_close_over25", "AvgC<2.5": "avg_close_under25",
    "Avg>2.5": "avg_open_over25", "Avg<2.5": "avg_open_under25",
    "AvgAHH": "avg_ah_home", "AvgAHA": "avg_ah_away", "AHh": "ah_line",
}

INT_FIELDS = {
    "home_goals", "away_goals", "ht_home_goals", "ht_away_goals",
    "home_shots", "away_shots", "home_shots_ot", "away_shots_ot",
    "home_fouls", "away_fouls", "home_corners", "away_corners",
    "home_yellow", "away_yellow", "home_red", "away_red",
}
FLOAT_FIELDS = {
    "pinnacle_close_h", "pinnacle_close_d", "pinnacle_close_a",
    "pinnacle_open_h", "pinnacle_open_d", "pinnacle_open_a",
    "b365_h", "b365_d", "b365_a", "avg_h", "avg_d", "avg_a",
    "pinnacle_close_over25", "pinnacle_close_under25",
    "avg_close_over25", "avg_close_under25",
    "avg_open_over25", "avg_open_under25", "avg_ah_home", "avg_ah_away", "ah_line",
    "b365_close_h", "b365_close_d", "b365_close_a",
    "max_close_h", "max_close_d", "max_close_a",
    "avg_close_h", "avg_close_d", "avg_close_a",
    "bfe_close_h", "bfe_close_d", "bfe_close_a",
    "bw_close_h", "bw_close_d", "bw_close_a",
    "max_open_h", "max_open_d", "max_open_a",
    "b365_close_over25", "b365_close_under25",
    "max_close_over25", "max_close_under25",
    "b365_open_over25", "b365_open_under25",
    "pinnacle_open_over25", "pinnacle_open_under25",
    "max_open_over25", "max_open_under25",
    "ah_close_line",
    "pinnacle_close_ah_home", "pinnacle_close_ah_away",
    "b365_close_ah_home", "b365_close_ah_away",
    "max_close_ah_home", "max_close_ah_away",
    "avg_close_ah_home", "avg_close_ah_away",
    "pinnacle_open_ah_home", "pinnacle_open_ah_away",
    "b365_open_ah_home", "b365_open_ah_away",
}


@dataclass
class SeasonLoad:
    league: str
    season: int          # start year: 2025 => 2025/26
    rows: List[dict] = field(default_factory=list)
    skipped: int = 0


def season_code(season: int) -> str:
    """2025 -> '2526'."""
    return f"{season % 100:02d}{(season + 1) % 100:02d}"


def _parse_date(raw: str) -> Optional[str]:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _coerce(field_name: str, raw: str):
    raw = (raw or "").strip()
    if raw == "":
        return None
    if field_name in INT_FIELDS:
        try:
            return int(float(raw))
        except ValueError:
            return None
    if field_name in FLOAT_FIELDS:
        try:
            return float(raw)
        except ValueError:
            return None
    return raw


def parse_csv(text: str, league: str, season: int) -> SeasonLoad:
    load = SeasonLoad(league=league, season=season)
    reader = csv.DictReader(io.StringIO(text))
    for raw_row in reader:
        if not (raw_row.get("HomeTeam") or "").strip():
            load.skipped += 1          # trailing blank lines are normal
            continue
        row: Dict[str, object] = {"league": league, "season": season}
        for src, dst in COLUMN_MAP.items():
            if src in raw_row:
                row[dst] = _coerce(dst, raw_row[src])
        date = _parse_date(str(row.get("date") or ""))
        if date is None:
            load.skipped += 1
            continue
        row["date"] = date
        if row.get("home_goals") is None or row.get("away_goals") is None:
            load.skipped += 1          # fixture listed but not yet played
            continue
        load.rows.append(row)
    return load


def fetch_season(league: str, season: int) -> SeasonLoad:
    """Download one league-season. Raises if the league is unknown."""
    if league not in LEAGUES:
        raise KeyError(f"unknown league {league!r}; known: {sorted(LEAGUES)}")
    code = LEAGUES[league][2]
    url = f"{BASE_URL}/{season_code(season)}/{code}.csv"
    resp = get(url)
    text = resp.content.decode("utf-8-sig", errors="replace")
    if not text.lstrip().lower().startswith("div"):
        # football-data serves an HTML 404 page for seasons that don't exist yet
        raise ValueError(f"{url} did not return a CSV (season not published?)")
    return parse_csv(text, league, season)


def fetch_all(seasons: Iterator[int]) -> List[SeasonLoad]:
    out = []
    for season in seasons:
        for league in LEAGUES:
            try:
                out.append(fetch_season(league, season))
            except (ValueError, Exception) as exc:      # noqa: B014 - explicit
                # Record and continue; a missing season is not fatal, but it is
                # never silent (v1 printed nothing and exited 0).
                print(f"  !! {league} {season}: {exc}")
    return out
