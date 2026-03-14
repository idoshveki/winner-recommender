"""
Head-to-head record between two teams via SofaScore.
Uses /event/{id}/h2h — needs the SofaScore event ID.
"""

from src.data.fetch_sofascore import _get


def get_h2h(event_id: int) -> dict:
    """
    Returns H2H summary for the two teams in event_id.
    Fields: home_wins, draws, away_wins, total, home_win_pct, away_win_pct
    """
    data = _get(f"/event/{event_id}/h2h")
    events = data.get("events", [])

    if not events:
        return _empty_h2h()

    home_team_id = data.get("homeTeam", {}).get("id")

    home_wins = draws = away_wins = 0
    home_goals = away_goals = 0

    for e in events[-10:]:  # last 10 H2H meetings
        status = e.get("status", {}).get("type", "")
        if status != "finished":
            continue

        hs = e.get("homeScore", {}).get("current", 0) or 0
        as_ = e.get("awayScore", {}).get("current", 0) or 0
        eid_home = e.get("homeTeam", {}).get("id")

        # Normalise: "home" = the team that is home in the upcoming match
        if eid_home == home_team_id:
            gf, ga = hs, as_
        else:
            gf, ga = as_, hs

        home_goals += gf
        away_goals += ga

        if gf > ga:
            home_wins += 1
        elif gf == ga:
            draws += 1
        else:
            away_wins += 1

    total = home_wins + draws + away_wins
    if total == 0:
        return _empty_h2h()

    return {
        "total": total,
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "home_win_pct": round(home_wins / total, 3),
        "draw_pct": round(draws / total, 3),
        "away_win_pct": round(away_wins / total, 3),
        "avg_goals_home": round(home_goals / total, 2),
        "avg_goals_away": round(away_goals / total, 2),
    }


def _empty_h2h():
    return {
        "total": 0, "home_wins": 0, "draws": 0, "away_wins": 0,
        "home_win_pct": None, "draw_pct": None, "away_win_pct": None,
        "avg_goals_home": None, "avg_goals_away": None,
    }
