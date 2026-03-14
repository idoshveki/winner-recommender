"""
Home-specific and Away-specific form split.
A team might be 5W at home but 0W away — overall form hides this.
"""

from datetime import datetime
from src.data.fetch_sofascore import _get


def get_split_form(team_id: int, before_date: str, n: int = 5) -> dict:
    """
    Returns separate home and away form records before before_date.
    """
    cutoff = datetime.strptime(before_date, "%Y-%m-%d")

    home_matches = []
    away_matches = []

    for page in range(3):
        try:
            data = _get(f"/team/{team_id}/events/last/{page}")
        except Exception:
            break

        events = data.get("events", [])
        if not events:
            break

        for e in reversed(events):
            status = e.get("status", {}).get("type", "")
            if status != "finished":
                continue
            ts = e.get("startTimestamp", 0)
            match_date = datetime.fromtimestamp(ts)
            if match_date >= cutoff:
                continue

            is_home = (e.get("homeTeam", {}).get("id") == team_id)
            hs = e.get("homeScore", {}).get("current", 0) or 0
            as_ = e.get("awayScore", {}).get("current", 0) or 0
            gf = hs if is_home else as_
            ga = as_ if is_home else hs
            result = "W" if gf > ga else ("D" if gf == ga else "L")

            m = {"result": result, "gf": gf, "ga": ga}
            if is_home:
                home_matches.append(m)
            else:
                away_matches.append(m)

        if len(home_matches) >= n and len(away_matches) >= n:
            break

    return {
        "home": _summarise(home_matches[:n], "home"),
        "away": _summarise(away_matches[:n], "away"),
    }


def _summarise(matches: list, venue: str) -> dict:
    if not matches:
        return {"played": 0, "wins": 0, "draws": 0, "losses": 0,
                "points": 0, "goals_for": 0, "goals_against": 0, "form_str": ""}
    wins   = sum(1 for m in matches if m["result"] == "W")
    draws  = sum(1 for m in matches if m["result"] == "D")
    losses = sum(1 for m in matches if m["result"] == "L")
    return {
        "played":         len(matches),
        "wins":           wins,
        "draws":          draws,
        "losses":         losses,
        "points":         wins * 3 + draws,
        "goals_for":      sum(m["gf"] for m in matches),
        "goals_against":  sum(m["ga"] for m in matches),
        "form_str":       "".join(m["result"] for m in matches),
    }
