"""Market settlement. One function per market type, used by the backtest, the
experiment and (eventually) the live settler, so a bet can never be graded one
way in research and another in production.

The subtle cases, all of which are easy to get wrong silently:

  * Totals on an INTEGER line push. Over 3.0 wins on 4+, loses on 0-2, and
    returns the stake on exactly 3.
  * Handicaps are asymmetric around a whole number. "+0.5" covers when counts
    are EQUAL; "-0.5" does not. A half-point handicap can never push; a whole
    number one can.
  * A push is not a win. v1 had no push handling at all, which quietly
    overstated every integer-line result.
"""
from __future__ import annotations

from typing import Optional

WIN, LOSS, PUSH = 1.0, 0.0, None


def _is_integer(x: float) -> bool:
    return abs(x - round(x)) < 1e-9


def settle_total(total: int, line: float, side: str) -> Optional[float]:
    """Over/under a total. Returns 1.0 win, 0.0 loss, None push."""
    side = side.strip().lower()
    if side not in ("over", "under"):
        raise ValueError(f"side must be over/under, got {side!r}")
    if _is_integer(line) and total == int(round(line)):
        return PUSH
    if side == "over":
        return WIN if total > line else LOSS
    return WIN if total < line else LOSS


def settle_handicap(own: int, opponent: int, point: float) -> Optional[float]:
    """A team handicap: the team covers when own + point > opponent.

    `point` is the handicap attached to the team being backed. Cards handicaps
    are quoted as e.g. Brighton -0.5 (strictly more cards than the opponent)
    against Liverpool +0.5 (more cards OR equal).
    """
    margin = own + point - opponent
    if abs(margin) < 1e-9:
        return PUSH                    # only reachable on whole-number lines
    return WIN if margin > 0 else LOSS


def pnl(result: Optional[float], price: float, stake: float = 1.0) -> float:
    """Profit (not return) for a settled bet. A push returns the stake."""
    if result is PUSH:
        return 0.0
    return stake * (price - 1.0) if result == WIN else -stake
