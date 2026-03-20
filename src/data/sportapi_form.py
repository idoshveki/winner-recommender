"""
SportAPI (RapidAPI) real-time form module.
Replaces CSV-based get_form() in send_weekly.py with live data.

Usage:
    from src.data.sportapi_form import get_fixtures_with_ids, get_league_form
"""

import os
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

def _get_api_key():
    # Streamlit Cloud secrets take priority, fallback to .env
    try:
        import streamlit as st
        return st.secrets["SOFASCORE_API_KEY"]
    except Exception:
        return os.getenv("SOFASCORE_API_KEY")

API_KEY = (_get_api_key() or "").strip()
HOST = "sportapi7.p.rapidapi.com"
BASE = f"https://{HOST}/api/v1"
HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": HOST,
}

# SofaScore unique tournament IDs → our internal league names
TOURNAMENT_IDS = {
    17: "EPL",
    8:  "La_Liga",
    35: "Bundesliga",
    23: "Serie_A",
}
# Reverse: league name → tournament ID
LEAGUE_TO_TOURNEY = {v: k for k, v in TOURNAMENT_IDS.items()}


def _get(path, params=None, retries=3):
    for attempt in range(retries):
        r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params, timeout=10)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        time.sleep(0.35)
        return r.json()
    r.raise_for_status()


def get_fixtures_with_ids(days=5):
    """
    Return upcoming fixtures for our 4 leagues in the next `days` days.
    Each fixture includes SportAPI team IDs needed for form lookups.

    Returns list of dicts:
        league, tournament_id, home, home_id, away, away_id, date, kickoff_ts
    """
    fixtures = []
    seen = set()
    for i in range(days + 1):
        d = (datetime.today() + timedelta(days=i)).strftime("%Y-%m-%d")
        data = _get(f"/sport/football/scheduled-events/{d}")
        for e in data.get("events", []):
            tid = e.get("tournament", {}).get("uniqueTournament", {}).get("id")
            if tid not in TOURNAMENT_IDS:
                continue
            status = e.get("status", {}).get("type", "")
            if status in ("finished", "cancelled"):
                continue
            key = (e["homeTeam"]["id"], e["awayTeam"]["id"])
            if key in seen:
                continue
            seen.add(key)
            fixtures.append({
                "league":        TOURNAMENT_IDS[tid],
                "tournament_id": tid,
                "event_id":      e["id"],
                "home":          e["homeTeam"]["name"],
                "home_id":       e["homeTeam"]["id"],
                "away":          e["awayTeam"]["name"],
                "away_id":       e["awayTeam"]["id"],
                "date":          datetime.fromtimestamp(e["startTimestamp"]).strftime("%Y-%m-%d"),
                "kickoff_ts":    e["startTimestamp"],
            })
    return fixtures


def get_league_form(team_id, tournament_id, venue, n=5):
    """
    Compute form features for a team from their last n venue-specific league matches.

    Args:
        team_id:       SportAPI team ID
        tournament_id: SofaScore unique tournament ID (17=EPL, 8=La_Liga, etc.)
        venue:         'home' or 'away'
        n:             number of recent venue games to use (default 5)

    Returns dict matching the shape of send_weekly.get_form():
        pts, trend, dr10, gf5, ga5, streak, lstreak
    """
    win_char  = "H" if venue == "home" else "A"
    lose_char = "A" if venue == "home" else "H"

    all_matches = []
    for page in range(4):
        data = _get(f"/team/{team_id}/events/last/{page}")
        events = data.get("events", [])
        if not events:
            break
        for e in events:
            tid = e.get("tournament", {}).get("uniqueTournament", {}).get("id")
            if tid != tournament_id:
                continue
            if e.get("status", {}).get("type", "") != "finished":
                continue
            is_home = (e["homeTeam"]["id"] == team_id)
            if venue == "home" and not is_home:
                continue
            if venue == "away" and is_home:
                continue
            hs  = e["homeScore"].get("current", 0) or 0
            as_ = e["awayScore"].get("current", 0) or 0
            gf  = hs if is_home else as_
            ga  = as_ if is_home else hs
            if gf > ga:   result = win_char
            elif gf == ga: result = "D"
            else:          result = lose_char
            all_matches.append({
                "result": result, "gf": gf, "ga": ga,
                "date": datetime.fromtimestamp(e["startTimestamp"]),
            })
        # stop paging once we have enough
        if len(all_matches) >= n * 3:
            break

    if not all_matches:
        return {"pts": 0, "trend": 0, "dr10": 0.25, "gf5": 1.2, "ga5": 1.2,
                "streak": 0, "lstreak": 0}

    all_matches.sort(key=lambda x: x["date"])
    last_n  = all_matches[-n:]
    last_10 = all_matches[-10:]

    pts    = sum(3 if m["result"] == win_char else (1 if m["result"] == "D" else 0) for m in last_n)
    recent3 = sum(3 if m["result"] == win_char else (1 if m["result"] == "D" else 0) for m in last_n[-3:])
    prior3  = sum(3 if m["result"] == win_char else (1 if m["result"] == "D" else 0) for m in last_n[-6:-3])
    dr10   = sum(1 for m in last_10 if m["result"] == "D") / max(len(last_10), 1)
    gf5    = sum(m["gf"] for m in last_n) / max(len(last_n), 1)
    ga5    = sum(m["ga"] for m in last_n) / max(len(last_n), 1)

    results_list = [m["result"] for m in last_10]
    streak = lstreak = 0
    for r in reversed(results_list):
        if r == win_char:  streak  += 1
        else: break
    for r in reversed(results_list):
        if r == lose_char: lstreak += 1
        else: break

    return {
        "pts":     pts,
        "trend":   recent3 - prior3,
        "dr10":    round(dr10, 2),
        "gf5":     round(gf5, 1),
        "ga5":     round(ga5, 1),
        "streak":  streak,
        "lstreak": lstreak,
    }
