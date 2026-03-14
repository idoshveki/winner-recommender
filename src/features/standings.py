"""
League standings via SofaScore.
Maps tournament IDs to their current season IDs, then fetches standings.
"""

from src.data.fetch_sofascore import _get

# SofaScore unique-tournament ID → current season ID (update each season)
SEASON_IDS = {
    17:  61627,  # Premier League 2025/26
    8:   61643,  # La Liga 2025/26
    35:  63814,  # Bundesliga 2025/26
    23:  61644,  # Serie A 2025/26
    34:  61645,  # Ligue 1 2025/26
    7:   61571,  # Champions League 2025/26
    679: 61572,  # Europa League 2025/26
    390: 61573,  # Conference League 2025/26
    156: 63376,  # Israeli Premier League 2025/26
}

_cache = {}  # {tournament_id: {team_id: row}}


def _load_standings(tournament_id: int):
    if tournament_id in _cache:
        return _cache[tournament_id]

    season_id = SEASON_IDS.get(tournament_id)
    if not season_id:
        return {}

    try:
        data = _get(f"/unique-tournament/{tournament_id}/season/{season_id}/standings/total")
        standings = data.get("standings", [])
        by_team = {}
        for group in standings:
            for row in group.get("rows", []):
                team_id = row.get("team", {}).get("id")
                if team_id:
                    by_team[team_id] = {
                        "position":        row.get("position"),
                        "points":          row.get("points"),
                        "wins":            row.get("wins"),
                        "draws":           row.get("draws"),
                        "losses":          row.get("losses"),
                        "goals_for":       row.get("scoresFor"),
                        "goals_against":   row.get("scoresAgainst"),
                        "matches_played":  row.get("matches"),
                    }
        _cache[tournament_id] = by_team
        return by_team
    except Exception:
        return {}


def get_team_standing(team_id: int, tournament_id: int) -> dict:
    """Returns standing row for a team in a tournament, or empty dict."""
    standings = _load_standings(tournament_id)
    return standings.get(team_id, {})


def standing_summary(home_id, away_id, tournament_id) -> dict:
    """Compare two teams' standings in a tournament."""
    h = get_team_standing(home_id, tournament_id)
    a = get_team_standing(away_id, tournament_id)
    return {"home": h, "away": a}
