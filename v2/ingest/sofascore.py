"""SofaScore (via RapidAPI) client.

SofaScore's integer ids are v2's canonical identity for teams and matches.
That is the whole point: v1 reconciled four different naming conventions with
three hand-maintained dicts that failed open, silently dropping fixtures.

Note the endpoint churn risk: the date-keyed /sport/football/scheduled-events/
route was removed upstream in Aug 2026 and broke v1's weekly run. Every route
here is one call site so a replacement is a one-line change.
"""
from __future__ import annotations

import time
from typing import Dict, Iterator, List, Optional

from v2.lib.config import LEAGUES, SOFASCORE_KEY, TOURNAMENT_TO_LEAGUE
from v2.lib.http import get

HOST = "sportapi7.p.rapidapi.com"
BASE = f"https://{HOST}/api/v1"
_PAUSE = 0.35          # RapidAPI rate limit courtesy


def _headers() -> Dict[str, str]:
    if not SOFASCORE_KEY:
        raise RuntimeError("SOFASCORE_API_KEY is not set")
    return {"x-rapidapi-key": SOFASCORE_KEY, "x-rapidapi-host": HOST}


def _get(path: str, params: Optional[dict] = None) -> dict:
    resp = get(f"{BASE}{path}", headers=_headers(), params=params)
    time.sleep(_PAUSE)
    return resp.json()


def credits_remaining() -> Optional[int]:
    resp = get(f"{BASE}/unique-tournament/17", headers=_headers())
    raw = resp.headers.get("x-ratelimit-requests-remaining")
    return int(raw) if raw else None


def seasons(tournament_id: int) -> List[dict]:
    """Newest first. [{'id': 96668, 'year': '26/27'}, ...]"""
    return _get(f"/unique-tournament/{tournament_id}/seasons").get("seasons", [])


def current_season_id(tournament_id: int) -> Optional[int]:
    s = seasons(tournament_id)
    return s[0]["id"] if s else None


def teams_in_season(tournament_id: int, season_id: int) -> List[dict]:
    """Every team in a league-season, from the final standings table."""
    data = _get(f"/unique-tournament/{tournament_id}/season/{season_id}/standings/total")
    out = []
    for table in data.get("standings", []):
        for row in table.get("rows", []):
            t = row.get("team") or {}
            if t.get("id"):
                out.append({
                    "sofascore_id": t["id"],
                    "name": t.get("name"),
                    "short_name": t.get("shortName"),
                    "slug": t.get("slug"),
                })
    return out


def upcoming_fixtures(tournament_id: int, season_id: int) -> List[dict]:
    data = _get(f"/unique-tournament/{tournament_id}/season/{season_id}/events/next/0")
    return data.get("events", [])


def match_statistics(event_id: int) -> Dict[str, int]:
    """Corners and cards for a finished match, keyed by event id.

    Returns {} when the match has no stats yet - the caller must treat that as
    'not ready', never as zero. v1's corner scorer read absent history as 0.0
    and emitted maximum-confidence phantom picks as a result.
    """
    data = _get(f"/event/{event_id}/statistics")
    wanted = {
        "corner kicks": ("home_corners", "away_corners"),
        "yellow cards": ("home_yellow", "away_yellow"),
        "red cards":    ("home_red", "away_red"),
        "fouls":        ("home_fouls", "away_fouls"),
        "total shots":  ("home_shots", "away_shots"),
        "shots on target": ("home_shots_ot", "away_shots_ot"),
    }
    out: Dict[str, int] = {}
    for period in data.get("statistics", []):
        if period.get("period") != "ALL":
            continue
        for group in period.get("groups", []):
            for item in group.get("statisticsItems", []):
                key = (item.get("name") or "").strip().lower()
                if key in wanted:
                    h_field, a_field = wanted[key]
                    try:
                        out[h_field] = int(item["home"])
                        out[a_field] = int(item["away"])
                    except (KeyError, TypeError, ValueError):
                        pass
    return out


def iter_league_seasons(from_year: int = 2020) -> Iterator[tuple]:
    """Yield (league, tournament_id, season_id, year_label) for each season."""
    for league, (tid, _, _) in LEAGUES.items():
        for s in seasons(tid):
            label = s.get("year", "")
            head = label.split("/")[0].strip()
            try:
                # labels come as both '24/25' and '1969/1970'
                if len(head) == 4:
                    start = int(head)
                else:
                    # '24' -> 2024 but '70' -> 1970; SofaScore carries
                    # seasons back to the 1960s on some tournaments.
                    n = int(head)
                    start = 2000 + n if n <= 30 else 1900 + n
            except (ValueError, IndexError):
                continue
            if start >= from_year:
                yield league, tid, s["id"], label


def finished_events(tournament_id: int, season_id: int, page: int = 0) -> List[dict]:
    """Most recently completed matches for a league-season."""
    data = _get(f"/unique-tournament/{tournament_id}/season/{season_id}/events/last/{page}")
    return data.get("events", [])
