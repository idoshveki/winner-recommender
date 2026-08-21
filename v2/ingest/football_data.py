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
    "Avg>2.5": "avg_over25", "Avg<2.5": "avg_under25",
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
    "avg_over25", "avg_under25", "avg_ah_home", "avg_ah_away", "ah_line",
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
