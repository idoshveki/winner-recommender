"""Central configuration. Every constant the pipeline depends on lives here."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "v2"
load_dotenv(ROOT / ".env")

# ── credentials ───────────────────────────────────────────────────────────
SOFASCORE_KEY = (os.getenv("SOFASCORE_API_KEY") or "").strip()
ODDS_API_KEY = (os.getenv("ODDS_API_KEY") or "").strip()
RESEND_API_KEY = (os.getenv("RESEND_API_KEY") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

# ── leagues ───────────────────────────────────────────────────────────────
# league name -> (sofascore unique-tournament id, odds-api sport key,
#                 football-data.co.uk division code)
LEAGUES = {
    "EPL":        (17, "soccer_epl",                "E0"),
    "La_Liga":    (8,  "soccer_spain_la_liga",      "SP1"),
    "Bundesliga": (35, "soccer_germany_bundesliga", "D1"),
    "Serie_A":    (23, "soccer_italy_serie_a",      "I1"),
}
TOURNAMENT_TO_LEAGUE = {tid: name for name, (tid, _, _) in LEAGUES.items()}
SPORT_TO_LEAGUE = {sport: name for name, (_, sport, _) in LEAGUES.items()}

# ── markets ───────────────────────────────────────────────────────────────
MARKET_CARDS = "totals_cards"
MARKET_CORNERS = "totals_corners"
MARKET_H2H = "h2h"

# Odds API market keys -> our canonical market names
ODDS_API_MARKETS = {
    "alternate_totals_cards":   MARKET_CARDS,
    "alternate_totals_corners": MARKET_CORNERS,
    "h2h":                      MARKET_H2H,
}
# eu carries pinnacle (sharp); uk carries the betfair exchange + soft books.
ODDS_API_REGIONS = "eu,uk"

# Books we treat as sharp when forming the fair-value consensus.
SHARP_BOOKS = ("pinnacle", "betfair_ex_uk", "betfair_ex_eu")

# The lines we model and price. Chosen to bracket the observed means
# (cards 4.20, corners 9.66) rather than to match any book's headline line.
CARD_LINES = (2.5, 3.5, 4.5, 5.5)
CORNER_LINES = (8.5, 9.5, 10.5, 11.5)

# ── modelling ─────────────────────────────────────────────────────────────
# Temporal split. Nothing after TEST_START may influence any parameter.
TRAIN_END = "2025-06-30"   # through season 2024/25
VALID_END = "2026-03-09"   # 2025/26 up to where v1's history stops
# test = everything after VALID_END

# Minimum edge over the de-vigged sharp consensus before we recommend.
# The measured out-of-sample Brier gain is only 1.3-2.2%, so anything
# smaller than this is inside the noise and inside the bookmaker's margin.
MIN_EDGE = 0.05

MIN_HISTORY_MATCHES = 5   # per team, per venue, before a prediction is allowed
