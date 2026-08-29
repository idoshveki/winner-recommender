"""Feature construction for the cards and corners count models.

One module, used by training, backtesting and live inference alike. v1 had two
divergent implementations of its scorers, so "backtested" described different
code than what ran on Friday.

Every feature for a match is computed from matches STRICTLY BEFORE it. The
rolling stores are updated only after a row is emitted, which makes lookahead
structurally impossible rather than a thing to remember.

Each rolling value carries its sample size. The caller must refuse to predict
when n is too small: v1's corners scorer read empty history as 0.0, which
passed its `< 0.35` gate and emitted a maximum-confidence phantom pick.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

WINDOW = 12
HALFLIFE = 8.0          # matches; recent form counts for more
LEAGUE_WINDOW = 300
MIN_TEAM_MATCHES = 6


def _ewma(values: Iterable[float]) -> Optional[float]:
    vals = list(values)
    if not vals:
        return None
    decay = 0.5 ** (1.0 / HALFLIFE)
    weights = [decay ** (len(vals) - 1 - i) for i in range(len(vals))]
    return sum(v * w for v, w in zip(vals, weights)) / sum(weights)


def implied_1x2(h: Optional[float], d: Optional[float], a: Optional[float]):
    """Vig-free 1X2 probabilities. Returns None when any leg is missing."""
    if not (h and d and a):
        return None
    # psycopg returns numeric as Decimal; keep everything float from here on
    q = [1.0 / float(h), 1.0 / float(d), 1.0 / float(a)]
    total = sum(q)
    return tuple(x / total for x in q)


@dataclass
class FeatureRow:
    match_id: int
    league: str
    kickoff: object
    target_cards: Optional[int]
    target_corners: Optional[int]
    target_goals: Optional[int] = None
    target_card_diff: Optional[int] = None
    raw_home: Optional[int] = None      # home cards, for the difference model
    raw_away: Optional[int] = None
    features: Dict[str, float] = None
    n_obs_min: int = 0

    def usable(self) -> bool:
        return self.n_obs_min >= MIN_TEAM_MATCHES


class RollingState:
    """Venue-split for/against stores per team, plus a league baseline."""

    def __init__(self):
        self.for_home = defaultdict(lambda: deque(maxlen=WINDOW))
        self.for_away = defaultdict(lambda: deque(maxlen=WINDOW))
        self.against_home = defaultdict(lambda: deque(maxlen=WINDOW))
        self.against_away = defaultdict(lambda: deque(maxlen=WINDOW))
        self.league = defaultdict(lambda: deque(maxlen=LEAGUE_WINDOW))

    def snapshot(self, league: str, home: int, away: int, prefix: str) -> Tuple[Dict, int]:
        lg = self.league[league]
        league_mean = sum(lg) / len(lg) if len(lg) >= 40 else None
        parts = {
            f"{prefix}_home_for": _ewma(self.for_home[home]),
            f"{prefix}_home_against": _ewma(self.against_home[home]),
            f"{prefix}_away_for": _ewma(self.for_away[away]),
            f"{prefix}_away_against": _ewma(self.against_away[away]),
            f"{prefix}_league_mean": league_mean,
        }
        n = min(len(self.for_home[home]), len(self.for_away[away]),
                len(self.against_home[home]), len(self.against_away[away]))
        return parts, n

    def update(self, league: str, home: int, away: int, h_val: int, a_val: int) -> None:
        self.for_home[home].append(h_val)
        self.against_home[home].append(a_val)
        self.for_away[away].append(a_val)
        self.against_away[away].append(h_val)
        self.league[league].append(h_val + a_val)


def build(rows: List[dict]) -> List[FeatureRow]:
    """rows must be ordered by kickoff. Returns one FeatureRow per match."""
    cards = RollingState()
    corners = RollingState()
    shots = RollingState()
    goals = RollingState()
    fouls = RollingState()
    out: List[FeatureRow] = []

    for r in rows:
        league, home, away = r["league"], r["home_team_id"], r["away_team_id"]
        feats: Dict[str, float] = {}
        n_obs = []

        for state, prefix in ((cards, "cards"), (corners, "corners"), (shots, "shots"),
                              (goals, "goals"), (fouls, "fouls")):
            parts, n = state.snapshot(league, home, away, prefix)
            feats.update(parts)
            n_obs.append(n)

        market = implied_1x2(r.get("odds_h"), r.get("odds_d"), r.get("odds_a"))
        if market:
            ph, pd_, pa = market
            feats["mkt_p_home"] = ph
            feats["mkt_p_draw"] = pd_
            # Close matches are fought harder: the market's own view of how
            # even the tie is, for free, and already vig-free.
            feats["mkt_closeness"] = 1.0 - abs(ph - pa)
        else:
            feats["mkt_p_home"] = feats["mkt_p_draw"] = feats["mkt_closeness"] = None

        tot_goals = (r.get("home_goals"), r.get("away_goals"))
        tot_fouls = (r.get("home_fouls"), r.get("away_fouls"))
        tot_cards = (r.get("home_yellow"), r.get("away_yellow"))
        tot_corners = (r.get("home_corners"), r.get("away_corners"))
        out.append(FeatureRow(
            match_id=r["match_id"],
            league=league,
            kickoff=r["kickoff_utc"],
            target_cards=(sum(tot_cards) if None not in tot_cards else None),
            target_goals=(sum(tot_goals) if None not in tot_goals else None),
            target_card_diff=((r["home_yellow"] - r["away_yellow"])
                              if None not in tot_cards else None),
            raw_home=(r["home_yellow"] if None not in tot_cards else None),
            raw_away=(r["away_yellow"] if None not in tot_cards else None),
            target_corners=(sum(tot_corners) if None not in tot_corners else None),
            features=feats,
            n_obs_min=min(n_obs),
        ))

        # update AFTER emitting - this is what makes lookahead impossible
        if None not in tot_cards:
            cards.update(league, home, away, r["home_yellow"], r["away_yellow"])
        if None not in tot_corners:
            corners.update(league, home, away, r["home_corners"], r["away_corners"])
        if r.get("home_shots") is not None and r.get("away_shots") is not None:
            shots.update(league, home, away, r["home_shots"], r["away_shots"])
        if None not in tot_goals:
            goals.update(league, home, away, r["home_goals"], r["away_goals"])
        if None not in tot_fouls:
            fouls.update(league, home, away, r["home_fouls"], r["away_fouls"])

    return out


GOAL_FEATURES = [
    "goals_home_for", "goals_home_against", "goals_away_for", "goals_away_against",
    "goals_league_mean", "mkt_closeness", "mkt_p_home",
]
# Fouls are the causal mechanism behind cards and were never used by v1.
# Measured caveat in FINDINGS.md: in a hand-weighted blend they did not help.
# A fitted model gets to decide.
CARD_DIFF_FEATURES = [
    "fouls_home_for", "fouls_home_against", "fouls_away_for", "fouls_away_against",
    "cards_home_for", "cards_home_against", "cards_away_for", "cards_away_against",
    "mkt_p_home", "mkt_p_draw", "mkt_closeness",
]
CARD_FEATURES = [
    "cards_home_for", "cards_home_against", "cards_away_for", "cards_away_against",
    "cards_league_mean", "mkt_closeness", "mkt_p_draw",
]
CORNER_FEATURES = [
    "corners_home_for", "corners_home_against", "corners_away_for",
    "corners_away_against", "corners_league_mean",
    "shots_home_for", "shots_away_for", "mkt_closeness", "mkt_p_home",
]
