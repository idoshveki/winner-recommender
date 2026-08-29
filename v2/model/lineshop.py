"""Soft book vs sharp line. No model - just prices and outcomes."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from v2.model.devig import devig_shin
from v2.model.markets import LOSS, PUSH, WIN, settle_handicap, settle_total

THRESHOLDS = (0.00, 0.01, 0.02, 0.03, 0.05)


def devig_three_way(h: float, d: float, a: float) -> Tuple[float, float, float]:
    """Proportional de-vig for 1X2. Shin is defined for two-way markets; the
    three-way generalisation adds assumptions we do not need here, and the
    negative control will show whether proportional is good enough."""
    q = [1.0 / h, 1.0 / d, 1.0 / a]
    t = sum(q)
    return (q[0] / t, q[1] / t, q[2] / t)


def settle_1x2(home_goals: int, away_goals: int, pick: str) -> Optional[float]:
    result = "H" if home_goals > away_goals else ("A" if away_goals > home_goals else "D")
    return WIN if pick == result else LOSS


def candidates_1x2(row: dict, soft_prefix: str,
                   sharp_prefix: str = "pinnacle_close") -> List[dict]:
    """sharp_prefix must be from the SAME moment as the soft price.

    Selecting with Pinnacle's CLOSING price while betting a soft OPENING price
    is lookahead: at the moment you would place the bet, the closing line does
    not exist. That measures closing-line value - a diagnostic - not a strategy
    you could have executed.
    """
    h, d, a = (row.get(sharp_prefix + "_h"), row.get(sharp_prefix + "_d"),
               row.get(sharp_prefix + "_a"))
    if not (h and d and a):
        return []
    ph, pd_, pa = devig_three_way(float(h), float(d), float(a))
    out = []
    for pick, p, col in (("H", ph, "_h"), ("D", pd_, "_d"), ("A", pa, "_a")):
        b = row.get(soft_prefix + col)
        if not b:
            continue
        out.append(dict(market="1X2", pick=pick, p=p, price=float(b),
                        edge=p * float(b) - 1,
                        result=settle_1x2(row["home_goals"], row["away_goals"], pick)))
    return out


def candidates_ou25(row: dict, over_col: str, under_col: str,
                    sharp: str = "pinnacle_close") -> List[dict]:
    o, u = row.get(sharp + "_over25"), row.get(sharp + "_under25")
    if not (o and u):
        return []
    p_over = devig_shin(float(o), float(u))
    total = row["home_goals"] + row["away_goals"]
    out = []
    for side, p, col in (("over", p_over, over_col), ("under", 1 - p_over, under_col)):
        b = row.get(col)
        if not b:
            continue
        out.append(dict(market="OU2.5", pick=side, p=p, price=float(b),
                        edge=p * float(b) - 1,
                        result=settle_total(total, 2.5, side)))
    return out


def candidates_ah(row: dict, home_col: str, away_col: str,
                  sharp: str = "pinnacle_close", line_col: str = "ah_close_line") -> List[dict]:
    h, a = row.get(sharp + "_ah_home"), row.get(sharp + "_ah_away")
    line = row.get(line_col)
    if not (h and a) or line is None:
        return []
    p_home = devig_shin(float(h), float(a))
    hg, ag = row["home_goals"], row["away_goals"]
    out = []
    for side, p, col, point in (("home", p_home, home_col, float(line)),
                                ("away", 1 - p_home, away_col, -float(line))):
        b = row.get(col)
        if not b:
            continue
        own, opp = (hg, ag) if side == "home" else (ag, hg)
        out.append(dict(market="AH", pick=side, p=p, price=float(b),
                        edge=p * float(b) - 1,
                        result=settle_handicap(own, opp, point)))
    return out
