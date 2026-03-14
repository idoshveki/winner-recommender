"""
Calculate days of rest between matches for a team.
Uses SofaScore last-events to find when the team last played before a given date.
"""

from datetime import datetime
from src.data.fetch_sofascore import _get


def get_rest_days(team_id: int, before_date: str) -> int:
    """
    Returns number of days since team's last match before before_date.
    Returns None if not determinable.
    """
    cutoff = datetime.strptime(before_date, "%Y-%m-%d")

    for page in range(2):
        try:
            data = _get(f"/team/{team_id}/events/last/{page}")
        except Exception:
            return None

        events = data.get("events", [])
        # Find most recent finished match before cutoff
        for e in reversed(events):
            status = e.get("status", {}).get("type", "")
            if status != "finished":
                continue
            ts = e.get("startTimestamp", 0)
            match_date = datetime.fromtimestamp(ts)
            if match_date < cutoff:
                delta = cutoff - match_date
                return delta.days

    return None


def rest_label(days) -> str:
    if days is None:
        return "unknown"
    if days <= 3:
        return "fatigued"    # 3 days or less — very tight turnaround
    if days <= 5:
        return "normal"
    if days <= 9:
        return "rested"
    return "very_rested"     # 10+ days — long break
