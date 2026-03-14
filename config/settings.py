from dotenv import load_dotenv
import os

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Leagues Winner typically covers — using Odds API sport keys
SPORTS = [
    # "soccer_israel_premier_league",  # not available on The Odds API — use API-Football instead
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_fifa_world_cup_qualifiers_europe",  # Israel national team qualifiers
]

# Bookmakers to pull — Pinnacle is sharpest reference, rest for consensus
BOOKMAKERS = [
    "pinnacle",
    "bet365",
    "williamhill",
    "unibet",
    "betfair",
    "draftkings",
]

# Markets to fetch
MARKETS = {
    "h2h": "1X2 match outcome",
    "totals": "Over/Under goals",
    "btts": "Both teams to score",
    "h2h_lay": "Lay (exchange)",
}

# Value betting threshold — flag bets where our edge exceeds this
EDGE_THRESHOLD = 0.05  # 5%

# Kelly fraction (0.25 = quarter Kelly — conservative)
KELLY_FRACTION = 0.25

# Accumulator target
ACCUM_TARGET_ODDS = 10.0
ACCUM_MAX_LEGS = 7
