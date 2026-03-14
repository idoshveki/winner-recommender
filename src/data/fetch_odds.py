"""
Fetch odds from The Odds API.
Covers: 1X2, Over/Under, BTTS across all tracked sports.
Saves raw JSON to data/raw/ and parsed records to SQLite.
"""

import os
import json
import requests
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Load key directly from .env without full settings module
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4"
RAW_DIR = ROOT / "data" / "raw" / "odds"
DB_PATH = ROOT / "data" / "db" / "winner.db"

RAW_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SPORTS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_fifa_world_cup_qualifiers_europe",
]

MARKETS = ["h2h", "totals"]  # btts requires paid plan on some tiers

BOOKMAKERS = "pinnacle,bet365,williamhill,unibet,betfair_ex_eu,onexbet"


# ── DB setup ────────────────────────────────────────────────────────────────

def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS odds_raw (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at      TEXT,
            sport           TEXT,
            event_id        TEXT,
            home_team       TEXT,
            away_team       TEXT,
            commence_time   TEXT,
            bookmaker       TEXT,
            market          TEXT,
            outcome_name    TEXT,
            price           REAL,
            point           REAL
        );

        CREATE INDEX IF NOT EXISTS idx_odds_event ON odds_raw(event_id, bookmaker, market);
    """)
    conn.commit()


def save_odds(conn, sport, event, bookmaker_key, market_key, outcome):
    conn.execute("""
        INSERT INTO odds_raw
            (fetched_at, sport, event_id, home_team, away_team,
             commence_time, bookmaker, market, outcome_name, price, point)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        sport,
        event["id"],
        event["home_team"],
        event["away_team"],
        event["commence_time"],
        bookmaker_key,
        market_key,
        outcome.get("name"),
        outcome.get("price"),
        outcome.get("point"),  # None for h2h/btts, float for totals
    ))


# ── API calls ───────────────────────────────────────────────────────────────

def get_sports():
    """List all available sports (useful for finding sport keys)."""
    r = requests.get(f"{BASE_URL}/sports", params={"apiKey": API_KEY})
    r.raise_for_status()
    return r.json()


def get_odds(sport, markets=None, bookmakers=None):
    """Fetch odds for upcoming events in a sport.
    NOTE: 'regions' and 'bookmakers' are mutually exclusive in the API.
    We use 'bookmakers' for fine-grained control.
    """
    params = {
        "apiKey": API_KEY,
        "bookmakers": bookmakers or BOOKMAKERS,  # don't mix with regions
        "markets": ",".join(markets or ["h2h"]),
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    r = requests.get(f"{BASE_URL}/sports/{sport}/odds", params=params)
    r.raise_for_status()

    # Log quota remaining
    remaining = r.headers.get("x-requests-remaining", "?")
    used = r.headers.get("x-requests-used", "?")
    print(f"  [{sport}] quota used: {used} | remaining: {remaining}")

    return r.json()


# ── Main ────────────────────────────────────────────────────────────────────

def fetch_all(sports=None, save_raw=True, save_db=True):
    sports = sports or SPORTS
    conn = sqlite3.connect(DB_PATH) if save_db else None
    if conn:
        init_db(conn)

    all_events = []

    for sport in sports:
        print(f"\nFetching odds: {sport}")
        try:
            events = get_odds(sport, markets=MARKETS)
        except requests.HTTPError as e:
            print(f"  ERROR: {e}")
            continue

        if not events:
            print("  No upcoming events.")
            continue

        print(f"  {len(events)} events found")

        if save_raw:
            raw_file = RAW_DIR / f"{sport}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
            raw_file.write_text(json.dumps(events, indent=2))

        for event in events:
            all_events.append(event)
            if not conn:
                continue
            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    for outcome in market.get("outcomes", []):
                        save_odds(conn, sport, event, bookmaker["key"], market["key"], outcome)

    if conn:
        conn.commit()
        conn.close()
        print(f"\nSaved to {DB_PATH}")

    return all_events


def print_sample(events, n=3):
    """Print a readable sample of fetched events."""
    print("\n=== SAMPLE ODDS ===")
    for event in events[:n]:
        print(f"\n{event['home_team']} vs {event['away_team']}  ({event['sport_title']})")
        print(f"  Kickoff: {event['commence_time']}")
        for bm in event.get("bookmakers", [])[:2]:
            print(f"  [{bm['title']}]")
            for mkt in bm.get("markets", []):
                outcomes_str = "  |  ".join(
                    f"{o['name']}: {o['price']}" + (f" ({o.get('point')})" if o.get("point") else "")
                    for o in mkt["outcomes"]
                )
                print(f"    {mkt['key']:10}  {outcomes_str}")


if __name__ == "__main__":
    print(f"API key loaded: {'YES' if API_KEY else 'NO — check .env'}")
    events = fetch_all()
    if events:
        print_sample(events)
