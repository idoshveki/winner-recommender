"""The Odds API client, with credit accounting.

Credits are charged per event as markets x regions. The /events endpoint is
FREE (0 credits) and returns event ids and team names, so mapping is done for
nothing and credits are spent only on prices.

Every response's quota headers are recorded, because running out mid-week
silently is the kind of failure that looks like "no bets this week".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from v2.lib.config import (
    ODDS_API_KEY, ODDS_API_MARKETS, ODDS_API_REGIONS, LEAGUES,
)
from v2.lib.http import get

BASE = "https://api.the-odds-api.com/v4"


@dataclass
class Usage:
    remaining: Optional[int] = None
    used: Optional[int] = None
    last_cost: int = 0
    total_cost: int = 0
    calls: int = 0

    def record(self, headers) -> None:
        self.calls += 1
        rem = headers.get("x-requests-remaining")
        use = headers.get("x-requests-used")
        last = headers.get("x-requests-last")
        if rem is not None:
            self.remaining = int(float(rem))
        if use is not None:
            self.used = int(float(use))
        if last is not None:
            self.last_cost = int(float(last))
            self.total_cost += self.last_cost


USAGE = Usage()


def _key() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is not set")
    return ODDS_API_KEY


def list_events(sport: str) -> List[dict]:
    """Upcoming events for a sport. Costs 0 credits."""
    resp = get(f"{BASE}/sports/{sport}/events", params={"apiKey": _key()})
    USAGE.record(resp.headers)
    return resp.json()


def event_odds(
    sport: str,
    event_id: str,
    markets: List[str],
    regions: str = ODDS_API_REGIONS,
) -> dict:
    """Odds for one event. Costs len(markets) x len(regions) credits."""
    resp = get(
        f"{BASE}/sports/{sport}/events/{event_id}/odds",
        params={
            "apiKey": _key(),
            "regions": regions,
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
        },
    )
    USAGE.record(resp.headers)
    return resp.json()


def planned_cost(n_events: int, markets: List[str], regions: str) -> int:
    return n_events * len(markets) * len([r for r in regions.split(",") if r])


def canonical_market(odds_api_key: str) -> Optional[str]:
    return ODDS_API_MARKETS.get(odds_api_key)


def sport_key(league: str) -> str:
    return LEAGUES[league][1]
