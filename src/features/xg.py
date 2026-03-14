"""
xG (expected goals) data from Understat (free scraping).
Covers: EPL, La Liga, Bundesliga, Serie A, Ligue 1.
Data is embedded as JSON in a <script> tag on the page.
"""

import re
import json
import time
import requests
from functools import lru_cache

LEAGUE_MAP = {
    17:  "EPL",
    8:   "La_liga",
    35:  "Bundesliga",
    23:  "Serie_A",
    34:  "Ligue_1",
}

UNDERSTAT_BASE = "https://understat.com"

_team_xg_cache = {}  # (league_key, season) -> {team_name: [match_dicts]}


def _fetch_understat(league_key: str, season: int) -> dict:
    """Fetch team xG history from Understat for a given league/season."""
    cache_key = (league_key, season)
    if cache_key in _team_xg_cache:
        return _team_xg_cache[cache_key]

    url = f"{UNDERSTAT_BASE}/league/{league_key}/{season}"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        # xG data is in a script tag: var teamsData = JSON.parse('...')
        match = re.search(r"var teamsData\s*=\s*JSON\.parse\('(.+?)'\)", r.text)
        if not match:
            return {}
        raw = match.group(1).encode("utf-8").decode("unicode_escape")
        data = json.loads(raw)
        result = {}
        for team_id, team_info in data.items():
            name = team_info.get("title", "")
            history = team_info.get("history", [])
            result[name] = history
        _team_xg_cache[cache_key] = result
        time.sleep(0.5)
        return result
    except Exception as e:
        return {}


def _fuzzy_match(name: str, candidates: list) -> str:
    """Simple fuzzy match — find closest team name."""
    name_lower = name.lower()
    for c in candidates:
        if c.lower() == name_lower:
            return c
    for c in candidates:
        if name_lower in c.lower() or c.lower() in name_lower:
            return c
    # Try first word match
    first_word = name_lower.split()[0]
    for c in candidates:
        if first_word in c.lower():
            return c
    return ""


def get_team_xg(team_name: str, tournament_id: int, before_date: str, n: int = 5) -> dict:
    """
    Returns rolling xG stats for a team in the last n matches before before_date.
    Only works for top 5 leagues (tournament IDs 17, 8, 35, 23, 34).
    """
    league_key = LEAGUE_MAP.get(tournament_id)
    if not league_key:
        return _empty_xg()

    from datetime import datetime
    cutoff = datetime.strptime(before_date, "%Y-%m-%d")
    season = cutoff.year if cutoff.month >= 8 else cutoff.year - 1

    team_data = _fetch_understat(league_key, season)
    if not team_data:
        return _empty_xg()

    matched = _fuzzy_match(team_name, list(team_data.keys()))
    if not matched:
        return _empty_xg()

    history = team_data[matched]
    past = [m for m in history if datetime.strptime(m["date"][:10], "%Y-%m-%d") < cutoff]
    recent = past[-n:]

    if not recent:
        return _empty_xg()

    xg_for  = sum(float(m.get("xG", 0))  for m in recent)
    xg_ag   = sum(float(m.get("xGA", 0)) for m in recent)
    goals   = sum(int(m.get("scored", 0)) for m in recent)
    conceded = sum(int(m.get("missed", 0)) for m in recent)

    n_matches = len(recent)
    npxg_for  = sum(float(m.get("npxG",  0)) for m in recent)
    npxg_ag   = sum(float(m.get("npxGA", 0)) for m in recent)
    ppda_vals = [float(m["ppda"]["att"]) / float(m["ppda"]["def"])
                 for m in recent
                 if m.get("ppda") and float(m["ppda"].get("def", 0) or 1) > 0]
    ppda_avg  = round(sum(ppda_vals) / len(ppda_vals), 2) if ppda_vals else None

    # xPTS: expected points based on xG (simulate match outcomes via Poisson)
    xpts = sum(_xpts_from_xg(float(m.get("xG", 0)), float(m.get("xGA", 0))) for m in recent)

    return {
        # Standard xG
        "xg_for":            round(xg_for  / n_matches, 2),
        "xg_against":        round(xg_ag   / n_matches, 2),
        "xg_diff":           round((xg_for - xg_ag) / n_matches, 2),
        # Non-penalty xG (removes pk luck)
        "npxg_for":          round(npxg_for / n_matches, 2),
        "npxg_against":      round(npxg_ag  / n_matches, 2),
        # Finishing quality: scoring vs xG expected
        "goals_per_game":    round(goals    / n_matches, 2),
        "conceded_per_game": round(conceded / n_matches, 2),
        "finishing_edge":    round((goals - xg_for) / n_matches, 2),   # + means clinical, - means wasteful
        "defensive_edge":    round((xg_ag - conceded) / n_matches, 2), # + means solid keeper
        # Pressing intensity (lower PPDA = more aggressive press)
        "ppda":              ppda_avg,
        "press_intensity":   _press_label(ppda_avg),
        # Expected points (truer quality than actual pts)
        "xpts_per_game":     round(xpts / n_matches, 2),
        "overperforming":    goals > xg_for,
        "matches":           n_matches,
    }


def _xpts_from_xg(xg_home: float, xg_away: float) -> float:
    """
    Estimate expected points using Poisson.
    Approximation: P(win) ≈ logistic-style from xG difference.
    """
    import math
    # Simple Poisson-based win probability
    def poisson_pmf(k, lam):
        return (lam ** k) * math.exp(-lam) / math.factorial(k)

    p_win = p_draw = p_loss = 0.0
    for i in range(8):
        for j in range(8):
            p = poisson_pmf(i, max(xg_home, 0.1)) * poisson_pmf(j, max(xg_away, 0.1))
            if i > j:
                p_win += p
            elif i == j:
                p_draw += p
            else:
                p_loss += p
    return p_win * 3 + p_draw * 1


def _press_label(ppda):
    if ppda is None:
        return "unknown"
    if ppda < 7:
        return "very_high"   # intense gegenpressing (like Liverpool/Man City)
    if ppda < 10:
        return "high"
    if ppda < 14:
        return "medium"
    return "low"             # passive, sit-back style


def _empty_xg():
    return {
        "xg_for": None, "xg_against": None, "xg_diff": None,
        "npxg_for": None, "npxg_against": None,
        "goals_per_game": None, "conceded_per_game": None,
        "finishing_edge": None, "defensive_edge": None,
        "ppda": None, "press_intensity": "unknown",
        "xpts_per_game": None,
        "overperforming": None, "matches": 0,
    }
