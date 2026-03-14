"""
Fetch pre-match odds for past matches using The Odds API historical endpoint.
GET /v4/historical/sports/{sport}/odds?date=TIMESTAMP
Returns odds as they were at a specific point in time.
Each call costs more quota — we batch by match date to minimise calls.
"""

import os
import time
import json
import sqlite3
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

API_KEY  = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4"
DB_PATH  = ROOT / "data" / "db" / "winner.db"
RAW_DIR  = ROOT / "data" / "raw" / "historical_odds"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS historical_odds (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at      TEXT,
            snapshot_time   TEXT,
            sport           TEXT,
            event_id        TEXT,
            home_team       TEXT,
            away_team       TEXT,
            commence_time   TEXT,
            bookmaker       TEXT,
            market          TEXT,
            outcome_name    TEXT,
            price           REAL,
            point           REAL,
            UNIQUE(event_id, bookmaker, market, outcome_name, snapshot_time)
        );
        CREATE INDEX IF NOT EXISTS idx_hist_event
            ON historical_odds(event_id, bookmaker, market);
    """)
    conn.commit()


def fetch_snapshot(sport: str, snapshot_dt: datetime, markets="h2h,totals") -> list:
    """
    Fetch odds snapshot for all events in a sport at a given datetime.
    snapshot_dt should be ~2 hours before a match kickoff for pre-match odds.
    """
    iso = snapshot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "apiKey":      API_KEY,
        "date":        iso,
        "regions":     "eu",
        "markets":     markets,
        "oddsFormat":  "decimal",
        "dateFormat":  "iso",
    }
    r = requests.get(f"{BASE_URL}/historical/sports/{sport}/odds",
                     params=params, timeout=15)

    remaining = r.headers.get("x-requests-remaining", "?")
    used      = r.headers.get("x-requests-used", "?")

    if r.status_code == 422:
        print(f"    422 — snapshot too old or unavailable: {iso}")
        return []
    r.raise_for_status()

    data = r.json()
    events = data.get("data", [])
    print(f"    snapshot {iso}: {len(events)} events | quota used {used}, remaining {remaining}")
    return events


def save_snapshot(conn, sport: str, snapshot_dt: datetime, events: list):
    iso = snapshot_dt.isoformat()
    rows = 0
    for event in events:
        for bm in event.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                for outcome in mkt.get("outcomes", []):
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO historical_odds
                            (fetched_at, snapshot_time, sport, event_id, home_team,
                             away_team, commence_time, bookmaker, market, outcome_name, price, point)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (
                            datetime.now(timezone.utc).isoformat(),
                            iso, sport,
                            event["id"], event["home_team"], event["away_team"],
                            event["commence_time"],
                            bm["key"], mkt["key"],
                            outcome.get("name"), outcome.get("price"), outcome.get("point"),
                        ))
                        rows += 1
                    except Exception:
                        pass
    conn.commit()
    return rows


def fetch_season(sport: str, season_start: str, season_end: str,
                 step_hours: int = 24, markets: str = "h2h,totals"):
    """
    Walk through a date range, fetching one snapshot per day.
    Captures pre-match odds for all matches in that window.
    season_start / season_end: 'YYYY-MM-DD'
    """
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    start = datetime.strptime(season_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end   = datetime.strptime(season_end,   "%Y-%m-%d").replace(tzinfo=timezone.utc)
    now   = datetime.now(timezone.utc)

    current = start
    total_rows = 0

    while current <= min(end, now - timedelta(hours=2)):
        # Take snapshot at 12:00 UTC each day — catches most pre-match windows
        snapshot = current.replace(hour=12, minute=0, second=0)
        print(f"\n  Fetching snapshot: {snapshot.date()}")

        try:
            events = fetch_snapshot(sport, snapshot, markets=markets)
            if events:
                rows = save_snapshot(conn, sport, snapshot, events)
                total_rows += rows

                # Save raw JSON
                raw_file = RAW_DIR / f"{sport}_{snapshot.strftime('%Y%m%d')}.json"
                raw_file.write_text(json.dumps(events, indent=2))

        except requests.HTTPError as e:
            print(f"    HTTP error: {e}")
        except Exception as e:
            print(f"    Error: {e}")

        time.sleep(0.5)
        current += timedelta(hours=step_hours)

    conn.close()
    print(f"\nDone. Total rows saved: {total_rows}")


def get_prematch_odds(event_id: str, hours_before: int = 2) -> dict:
    """
    Pull consensus pre-match implied probabilities from historical_odds table.
    Returns {p_home, p_draw, p_away, source} or {}.
    """
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT outcome_name, AVG(price) as avg_price
        FROM historical_odds
        WHERE event_id = ? AND market = 'h2h'
        GROUP BY outcome_name
    """, (event_id,)).fetchall()

    # Also try Pinnacle specifically for reference line
    pinnacle = conn.execute("""
        SELECT outcome_name, price
        FROM historical_odds
        WHERE event_id = ? AND market = 'h2h' AND bookmaker = 'pinnacle'
        ORDER BY snapshot_time DESC
        LIMIT 10
    """, (event_id,)).fetchall()
    conn.close()

    if not rows:
        return {}

    odds_map = {r[0]: r[1] for r in rows}
    raw = {name: 1/odds for name, odds in odds_map.items() if odds and odds > 1}
    total = sum(raw.values())
    if total == 0:
        return {}

    p_home = p_draw = p_away = None
    home_team = None
    for name, prob in raw.items():
        norm = prob / total
        if "draw" in name.lower():
            p_draw = norm
        elif p_home is None:
            p_home = norm
            home_team = name
        else:
            p_away = norm

    if not all([p_home, p_draw, p_away]):
        return {}

    return {
        "p_home": round(p_home, 3),
        "p_draw": round(p_draw, 3),
        "p_away": round(p_away, 3),
        "has_pinnacle": len(pinnacle) > 0,
        "source": "historical_odds",
    }


if __name__ == "__main__":
    # Fetch entire current EPL season
    print("Fetching EPL 2025/26 historical odds...")
    print("(This walks day by day — each day = 2 quota credits)")
    fetch_season(
        sport="soccer_epl",
        season_start="2025-08-15",  # EPL 2025/26 start
        season_end="2026-03-12",    # up to yesterday
        step_hours=24,
        markets="h2h,totals",
    )
