"""
Download historical match data + bookmaker odds from football-data.co.uk (free).
Includes Pinnacle, Bet365, William Hill closing odds for every match.
Covers EPL going back 20+ seasons.

CSV columns we use:
  Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR (H/D/A)
  HTHG, HTAG, HTR                — Half-time scores
  HS, AS, HST, AST               — Shots / shots on target
  HC, AC                         — Corners
  HY, AY, HR, AR                 — Yellow/Red cards
  B365H, B365D, B365A            — Bet365
  PSH,  PSD,  PSA                — Pinnacle (sharpest reference)
  AvgH, AvgD, AvgA               — Market average
  Avg>2.5, Avg<2.5               — Over/Under average
  AHh, AvgAHH, AvgAHA            — Asian handicap line + odds
"""

import io
import sqlite3
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime

ROOT    = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "db" / "winner.db"
RAW_DIR = ROOT / "data" / "raw" / "football_data"
RAW_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://www.football-data.co.uk/mmz4281"

# League codes on football-data.co.uk
LEAGUES = {
    "EPL":        "E0",
    "Championship": "E1",
    "La_Liga":    "SP1",
    "Bundesliga": "D1",
    "Serie_A":    "I1",
    "Ligue_1":    "F1",
}

# Seasons: "2526" = 2025/26, "2425" = 2024/25, etc.
def season_code(year: int) -> str:
    """year = start year of season. E.g. 2024 → '2425'"""
    return f"{str(year)[2:]}{str(year+1)[2:]}"


def download_season(league_key: str, start_year: int) -> pd.DataFrame:
    """Download one season CSV. Returns DataFrame or empty."""
    league_code = LEAGUES.get(league_key)
    if not league_code:
        raise ValueError(f"Unknown league: {league_key}")

    sc  = season_code(start_year)
    url = f"{BASE_URL}/{sc}/{league_code}.csv"

    r = requests.get(url, timeout=15)
    if r.status_code == 404:
        print(f"  Not found: {url}")
        return pd.DataFrame()
    r.raise_for_status()

    # Save raw
    raw_path = RAW_DIR / f"{league_key}_{sc}.csv"
    raw_path.write_bytes(r.content)

    df = pd.read_csv(io.BytesIO(r.content), encoding="latin-1")
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTR"])
    df["league"]     = league_key
    df["start_year"] = start_year
    print(f"  {league_key} {sc}: {len(df)} matches")
    return df


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Standardise columns across seasons/leagues."""
    cols = {
        "Date":    "date_str",
        "HomeTeam":"home_team",
        "AwayTeam":"away_team",
        "FTHG":    "home_goals",
        "FTAG":    "away_goals",
        "FTR":     "result",       # H / D / A
        # Half-time
        "HTHG":    "ht_home_goals",
        "HTAG":    "ht_away_goals",
        "HTR":     "ht_result",    # H / D / A
        # Shots
        "HS":      "home_shots",
        "AS":      "away_shots",
        "HST":     "home_shots_ot",
        "AST":     "away_shots_ot",
        # Corners
        "HC":      "home_corners",
        "AC":      "away_corners",
        # Cards
        "HY":      "home_yellow",
        "AY":      "away_yellow",
        "HR":      "home_red",
        "AR":      "away_red",
        # Pinnacle
        "PSH":     "pinnacle_h",
        "PSD":     "pinnacle_d",
        "PSA":     "pinnacle_a",
        # Bet365
        "B365H":   "b365_h",
        "B365D":   "b365_d",
        "B365A":   "b365_a",
        # Market average 1X2
        "AvgH":    "avg_h",
        "AvgD":    "avg_d",
        "AvgA":    "avg_a",
        # Over/Under 2.5
        "Avg>2.5": "avg_over25",
        "Avg<2.5": "avg_under25",
        "P>2.5":   "pinnacle_over25",
        "P<2.5":   "pinnacle_under25",
        # Asian handicap line + avg odds
        "AHh":     "ah_line",
        "AvgAHH":  "avg_ah_home",
        "AvgAHA":  "avg_ah_away",
    }
    df = df.rename(columns={k: v for k, v in cols.items() if k in df.columns})

    # Parse date
    for fmt in ["%d/%m/%Y", "%d/%m/%y"]:
        try:
            df["date"] = pd.to_datetime(df["date_str"], format=fmt)
            break
        except Exception:
            pass

    # Implied probabilities from Pinnacle (or fallback to B365/Avg)
    for prefix, cols_h, cols_d, cols_a in [
        ("pinnacle", "pinnacle_h", "pinnacle_d", "pinnacle_a"),
        ("b365",     "b365_h",     "b365_d",     "b365_a"),
        ("avg",      "avg_h",      "avg_d",      "avg_a"),
    ]:
        if all(c in df.columns for c in [cols_h, cols_d, cols_a]):
            df[f"{prefix}_implied_h"] = 1 / df[cols_h]
            df[f"{prefix}_implied_d"] = 1 / df[cols_d]
            df[f"{prefix}_implied_a"] = 1 / df[cols_a]
            # Remove vig
            overround = df[f"{prefix}_implied_h"] + df[f"{prefix}_implied_d"] + df[f"{prefix}_implied_a"]
            df[f"{prefix}_prob_h"] = df[f"{prefix}_implied_h"] / overround
            df[f"{prefix}_prob_d"] = df[f"{prefix}_implied_d"] / overround
            df[f"{prefix}_prob_a"] = df[f"{prefix}_implied_a"] / overround

    return df


def save_to_db(df: pd.DataFrame, conn: sqlite3.Connection):
    """Save normalised match data to SQLite. Uses REPLACE to update existing rows."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS matches_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            league          TEXT,
            season          INTEGER,
            date            TEXT,
            home_team       TEXT,
            away_team       TEXT,
            home_goals      INTEGER,
            away_goals      INTEGER,
            result          TEXT,
            -- Half-time
            ht_home_goals   INTEGER,
            ht_away_goals   INTEGER,
            ht_result       TEXT,
            -- Shots
            home_shots      INTEGER,
            away_shots      INTEGER,
            home_shots_ot   INTEGER,
            away_shots_ot   INTEGER,
            -- Corners
            home_corners    INTEGER,
            away_corners    INTEGER,
            -- Cards
            home_yellow     INTEGER,
            away_yellow     INTEGER,
            home_red        INTEGER,
            away_red        INTEGER,
            -- Odds
            pinnacle_h      REAL, pinnacle_d      REAL, pinnacle_a      REAL,
            pinnacle_prob_h REAL, pinnacle_prob_d  REAL, pinnacle_prob_a  REAL,
            b365_h          REAL, b365_d          REAL, b365_a          REAL,
            avg_h           REAL, avg_d           REAL, avg_a           REAL,
            avg_prob_h      REAL, avg_prob_d      REAL, avg_prob_a      REAL,
            avg_over25      REAL, avg_under25     REAL,
            pinnacle_over25 REAL, pinnacle_under25 REAL,
            ah_line         REAL, avg_ah_home     REAL, avg_ah_away     REAL,
            UNIQUE(league, date, home_team, away_team)
        );
    """)
    conn.commit()

    # Add new columns if upgrading an existing DB (ignore errors if already exist)
    new_cols = [
        ("ht_home_goals",   "INTEGER"),
        ("ht_away_goals",   "INTEGER"),
        ("ht_result",       "TEXT"),
        ("home_shots",      "INTEGER"),
        ("away_shots",      "INTEGER"),
        ("home_shots_ot",   "INTEGER"),
        ("away_shots_ot",   "INTEGER"),
        ("home_corners",    "INTEGER"),
        ("away_corners",    "INTEGER"),
        ("home_yellow",     "INTEGER"),
        ("away_yellow",     "INTEGER"),
        ("home_red",        "INTEGER"),
        ("away_red",        "INTEGER"),
        ("pinnacle_over25", "REAL"),
        ("pinnacle_under25","REAL"),
        ("ah_line",         "REAL"),
        ("avg_ah_home",     "REAL"),
        ("avg_ah_away",     "REAL"),
    ]
    for col, dtype in new_cols:
        try:
            conn.execute(f"ALTER TABLE matches_history ADD COLUMN {col} {dtype}")
        except Exception:
            pass
    conn.commit()

    inserted = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT OR REPLACE INTO matches_history
                (league, season, date, home_team, away_team, home_goals, away_goals, result,
                 ht_home_goals, ht_away_goals, ht_result,
                 home_shots, away_shots, home_shots_ot, away_shots_ot,
                 home_corners, away_corners,
                 home_yellow, away_yellow, home_red, away_red,
                 pinnacle_h, pinnacle_d, pinnacle_a,
                 pinnacle_prob_h, pinnacle_prob_d, pinnacle_prob_a,
                 b365_h, b365_d, b365_a,
                 avg_h, avg_d, avg_a,
                 avg_prob_h, avg_prob_d, avg_prob_a,
                 avg_over25, avg_under25,
                 pinnacle_over25, pinnacle_under25,
                 ah_line, avg_ah_home, avg_ah_away)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row.get("league"), row.get("start_year"),
                str(row.get("date", ""))[:10],
                row.get("home_team"), row.get("away_team"),
                row.get("home_goals"), row.get("away_goals"), row.get("result"),
                row.get("ht_home_goals"), row.get("ht_away_goals"), row.get("ht_result"),
                row.get("home_shots"), row.get("away_shots"),
                row.get("home_shots_ot"), row.get("away_shots_ot"),
                row.get("home_corners"), row.get("away_corners"),
                row.get("home_yellow"), row.get("away_yellow"),
                row.get("home_red"), row.get("away_red"),
                row.get("pinnacle_h"), row.get("pinnacle_d"), row.get("pinnacle_a"),
                row.get("pinnacle_prob_h"), row.get("pinnacle_prob_d"), row.get("pinnacle_prob_a"),
                row.get("b365_h"), row.get("b365_d"), row.get("b365_a"),
                row.get("avg_h"), row.get("avg_d"), row.get("avg_a"),
                row.get("avg_prob_h"), row.get("avg_prob_d"), row.get("avg_prob_a"),
                row.get("avg_over25"), row.get("avg_under25"),
                row.get("pinnacle_over25"), row.get("pinnacle_under25"),
                row.get("ah_line"), row.get("avg_ah_home"), row.get("avg_ah_away"),
            ))
            inserted += 1
        except Exception:
            pass
    conn.commit()
    return inserted


def fetch_all_epl(seasons: int = 5):
    """Download last N seasons of EPL data."""
    conn = sqlite3.connect(DB_PATH)
    current_year = datetime.today().year
    # EPL season starts in August; if before August use previous year
    if datetime.today().month < 8:
        current_year -= 1

    total = 0
    for i in range(seasons):
        year = current_year - i
        print(f"\nDownloading EPL {year}/{year+1}...")
        try:
            df = download_season("EPL", year)
            if df.empty:
                continue
            df = normalise(df)
            n  = save_to_db(df, conn)
            total += n
            print(f"  Saved {n} matches")
        except Exception as e:
            print(f"  Error: {e}")

    conn.close()
    print(f"\nTotal EPL matches in DB: {total}")
    return total


def reprocess_all_local():
    """Re-process all already-downloaded raw CSVs to populate new columns. No network needed."""
    conn = sqlite3.connect(DB_PATH)
    total = 0
    for csv_path in sorted(RAW_DIR.glob("*.csv")):
        name = csv_path.stem          # e.g. EPL_2425
        parts = name.rsplit("_", 1)
        if len(parts) != 2:
            continue
        league_key, sc = parts[0], parts[1]
        # sc like "2425" → start_year = 2024
        try:
            start_year = 2000 + int(sc[:2])
        except Exception:
            continue

        try:
            df = pd.read_csv(csv_path, encoding="latin-1")
            df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTR"])
            df["league"]     = league_key
            df["start_year"] = start_year
            df = normalise(df)
            n  = save_to_db(df, conn)
            total += n
            print(f"  {league_key} {sc}: {n} rows updated")
        except Exception as e:
            print(f"  Error {csv_path.name}: {e}")

    conn.close()
    print(f"\nDone. Total rows updated: {total}")


if __name__ == "__main__":
    print("Re-processing all local CSVs to populate new columns...")
    reprocess_all_local()
