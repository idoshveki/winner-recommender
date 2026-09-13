"""Systematic strategy sweep: is any CATEGORY of bet mispriced?

Everything before this asked "is our probability better than the book's".
This asks a different question that needs no model: does some class of bet -
draws, longshots, unders, opponents of famous teams - lose less than the
margin, or even win?

The danger is obvious. Test enough rules on one dataset and the best will look
excellent by chance. So the sweep is paired with a permutation null that
answers: given the market is exactly right, what does the BEST of N rules look
like? A rule only counts if it beats that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from v2.model.devig import devig_shin
from v2.model.lineshop import devig_three_way, settle_1x2
from v2.model.markets import LOSS, PUSH, WIN, settle_handicap, settle_total

# Price buckets test the favourite-longshot bias - the most documented anomaly
# in betting markets. Bettors overbet longshots, so books shade them.
BUCKETS = [("fav", 0.55, 1.01), ("mid-fav", 0.40, 0.55), ("mid", 0.25, 0.40),
           ("dog", 0.12, 0.25), ("longshot", 0.0, 0.12)]


def bucket_of(p: float) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= p < hi:
            return name
    return "fav"


@dataclass
class Selection:
    market: str        # 1X2 / OU25 / AH
    pick: str          # H D A / over under / home away
    p: float           # de-vigged market probability
    price: float       # price actually available (Pinnacle close)
    result: Optional[float]
    league: str
    season: int
    date: str
    home: str
    away: str

    @property
    def bucket(self) -> str:
        return bucket_of(self.p)


def selections(row: dict) -> List[Selection]:
    """Every bet available on one match at Pinnacle's closing price."""
    out: List[Selection] = []
    base = dict(league=row["league"], season=row["season"], date=row["date"],
                home=row["home_team"], away=row["away_team"])
    hg, ag = row.get("home_goals"), row.get("away_goals")
    if hg is None:
        return out

    h, d, a = (row.get("pinnacle_close_h"), row.get("pinnacle_close_d"),
               row.get("pinnacle_close_a"))
    if h and d and a:
        ph, pd_, pa = devig_three_way(float(h), float(d), float(a))
        for pick, p, price in (("H", ph, float(h)), ("D", pd_, float(d)), ("A", pa, float(a))):
            out.append(Selection("1X2", pick, p, price, settle_1x2(hg, ag, pick), **base))

    o, u = row.get("pinnacle_close_over25"), row.get("pinnacle_close_under25")
    if o and u:
        p_over = devig_shin(float(o), float(u))
        for pick, p, price in (("over", p_over, float(o)), ("under", 1 - p_over, float(u))):
            out.append(Selection("OU25", pick, p, price,
                                 settle_total(hg + ag, 2.5, pick), **base))

    ah_h, ah_a, line = (row.get("pinnacle_close_ah_home"),
                        row.get("pinnacle_close_ah_away"), row.get("ah_close_line"))
    if ah_h and ah_a and line is not None:
        p_home = devig_shin(float(ah_h), float(ah_a))
        for pick, p, price, pt in (("home", p_home, float(ah_h), float(line)),
                                   ("away", 1 - p_home, float(ah_a), -float(line))):
            own, opp = (hg, ag) if pick == "home" else (ag, hg)
            out.append(Selection("AH", pick, p, price,
                                 settle_handicap(own, opp, pt), **base))
    return out


@dataclass
class Rule:
    name: str
    test: Callable[[Selection], bool]
    family: str


def build_rules(teams: List[str]) -> List[Rule]:
    """A pre-specified rule space. Motivated categories first, then a sweep."""
    rules: List[Rule] = []

    def add(name, fn, family):
        rules.append(Rule(name, fn, family))

    # ── motivated structural hypotheses ──────────────────────────────────
    add("back every draw", lambda s: s.market == "1X2" and s.pick == "D", "draw")
    add("back every home", lambda s: s.market == "1X2" and s.pick == "H", "1x2")
    add("back every away", lambda s: s.market == "1X2" and s.pick == "A", "1x2")
    add("back every over 2.5", lambda s: s.market == "OU25" and s.pick == "over", "goals")
    add("back every under 2.5", lambda s: s.market == "OU25" and s.pick == "under", "goals")

    # favourite-longshot bias, per market
    for mk in ("1X2", "OU25", "AH"):
        for b, _, _ in BUCKETS:
            add(f"{mk}: back all {b}", 
                lambda s, mk=mk, b=b: s.market == mk and s.bucket == b, "flb")

    # league x selection
    for lg in ("EPL", "La_Liga", "Serie_A", "Bundesliga"):
        add(f"{lg}: back every draw",
            lambda s, lg=lg: s.market == "1X2" and s.pick == "D" and s.league == lg, "league")
        add(f"{lg}: back every home",
            lambda s, lg=lg: s.market == "1X2" and s.pick == "H" and s.league == lg, "league")
        add(f"{lg}: back every under",
            lambda s, lg=lg: s.market == "OU25" and s.pick == "under" and s.league == lg, "league")
        add(f"{lg}: back every over",
            lambda s, lg=lg: s.market == "OU25" and s.pick == "over" and s.league == lg, "league")

    # empty-stadium era: home advantage collapsed in 2020/21 and books were
    # widely reported to be slow adjusting
    add("2020/21 only: back away teams",
        lambda s: s.market == "1X2" and s.pick == "A" and s.season == 2020, "covid")
    add("2020/21 only: back home teams",
        lambda s: s.market == "1X2" and s.pick == "H" and s.season == 2020, "covid")

    # popular-team bias: the public backs famous clubs, shortening their price,
    # so the value should sit with their opponents
    for t in teams:
        add(f"oppose {t} (back their opponent 1X2)",
            lambda s, t=t: s.market == "1X2" and (
                (s.home == t and s.pick == "A") or (s.away == t and s.pick == "H")), "team")
        add(f"back {t}",
            lambda s, t=t: s.market == "1X2" and (
                (s.home == t and s.pick == "H") or (s.away == t and s.pick == "A")), "team")
    return rules
