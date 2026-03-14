"""
SofaScore API wrapper.
Fetches: scheduled events by date, match results, team recent form.
"""

import os
import time
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

API_KEY = os.getenv("SOFASCORE_API_KEY")
HOST = "sportapi7.p.rapidapi.com"
BASE = f"https://{HOST}/api/v1"

HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": HOST,
    "Content-Type": "application/json",
}

# Tournament IDs on SofaScore
TOURNAMENT_IDS = {
    17:  "Premier League",
    8:   "La Liga",
    35:  "Bundesliga",
    23:  "Serie A",
    34:  "Ligue 1",
    7:   "Champions League",
    679: "Europa League",
    390: "Europa Conference League",
    156: "Israeli Premier League",
}

TARGET_IDS = set(TOURNAMENT_IDS.keys())


def _get(path, params=None, retries=3):
    for attempt in range(retries):
        r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params, timeout=10)
        if r.status_code == 429:
            wait = 2 ** attempt  # 1s, 2s, 4s
            time.sleep(wait)
            continue
        r.raise_for_status()
        time.sleep(0.4)  # stay under rate limit
        return r.json()
    r.raise_for_status()  # raise on final attempt


def get_events_by_date(date: str) -> list[dict]:
    """date: 'YYYY-MM-DD'. Returns all football events that day."""
    data = _get(f"/sport/football/scheduled-events/{date}")
    events = data.get("events", [])
    # Filter to our target leagues only
    filtered = []
    for e in events:
        uid = e.get("tournament", {}).get("uniqueTournament", {}).get("id")
        if uid in TARGET_IDS:
            filtered.append(e)
    return filtered


def get_team_last_matches(team_id: int, page: int = 0) -> list[dict]:
    """Get a team's last matches (page 0 = most recent)."""
    data = _get(f"/team/{team_id}/events/last/{page}")
    return data.get("events", [])


def team_form(team_id: int, before_date: str, n: int = 5) -> dict:
    """
    Compute team form from last n matches BEFORE a given date.
    Returns: wins, draws, losses, goals_for, goals_against, points
    """
    cutoff = datetime.strptime(before_date, "%Y-%m-%d")
    matches = []

    for page in range(3):  # check up to 3 pages back
        events = get_team_last_matches(team_id, page)
        if not events:
            break
        for e in reversed(events):  # reversed = oldest first within page
            start = e.get("startTimestamp", 0)
            match_date = datetime.fromtimestamp(start)
            if match_date >= cutoff:
                continue
            status = e.get("status", {}).get("type", "")
            if status not in ("finished",):
                continue
            home_id = e.get("homeTeam", {}).get("id")
            home_score = e.get("homeScore", {}).get("current", 0) or 0
            away_score = e.get("awayScore", {}).get("current", 0) or 0
            is_home = (home_id == team_id)
            gf = home_score if is_home else away_score
            ga = away_score if is_home else home_score
            if gf > ga:
                result = "W"
            elif gf == ga:
                result = "D"
            else:
                result = "L"
            matches.append({"result": result, "gf": gf, "ga": ga, "date": match_date})
        if len(matches) >= n:
            break

    recent = sorted(matches, key=lambda x: x["date"], reverse=True)[:n]
    wins   = sum(1 for m in recent if m["result"] == "W")
    draws  = sum(1 for m in recent if m["result"] == "D")
    losses = sum(1 for m in recent if m["result"] == "L")
    gf     = sum(m["gf"] for m in recent)
    ga     = sum(m["ga"] for m in recent)
    pts    = wins * 3 + draws

    return {
        "played": len(recent),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "goals_for": gf,
        "goals_against": ga,
        "points": pts,
        "form_str": "".join(m["result"] for m in recent),
    }
