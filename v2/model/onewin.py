"""Predicting 1win's yellow-cards price for a match we have not seen quoted.

Measured on 10 hand-captured quotes (v2/data/onewin_quotes.json):

  * 1win's market is YELLOWS ONLY. Its implied mean equals Pinnacle's
    book-rule mean minus 2x the league's red-card rate, to within +0.046
    cards (sd 0.084) across the six matches where we hold both books.
  * 1win's two-way margin is 8.4% (sd 0.4), against Pinnacle's ~5.9%.

So 1win's price is not an independent opinion - it is Pinnacle's number,
correctly converted to a different quantity, with a wider margin. That is why
a single "average price" would be wrong in a way that manufactures edges: the
implied means across those 10 matches run from 3.48 to 4.74, and flattening
that spread turns real pricing variation into apparent model skill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from v2.model.devig import p_over_from_dist

# reds per match, from 8,676 matches of history
LEAGUE_REDS = {"La_Liga": 0.249, "Serie_A": 0.191, "Bundesliga": 0.143, "EPL": 0.116}
ONEWIN_MARGIN = 0.084          # measured, sd 0.004


def yellows_mean(pinnacle_book_mean: float, league: str) -> float:
    """Pinnacle prices yellows + 2x reds; 1win prices yellows."""
    return pinnacle_book_mean - 2.0 * LEAGUE_REDS.get(league, 0.19)


def predict_prices(
    pinnacle_book_mean: float,
    league: str,
    line: float,
    dispersion: Optional[float] = 55.0,
    margin: float = ONEWIN_MARGIN,
) -> Tuple[float, float]:
    """Expected (over, under) prices at 1win for a yellow-cards line.

    Margin is applied proportionally to both sides, which matched the captured
    quotes better than loading it onto the longshot.
    """
    mu = yellows_mean(pinnacle_book_mean, league)
    p = p_over_from_dist(mu, dispersion, line)
    p = min(max(p, 1e-4), 1 - 1e-4)
    scale = 1.0 + margin
    return (1.0 / (p * scale), 1.0 / ((1.0 - p) * scale))


@dataclass
class Quote:
    over: float
    under: float

    @property
    def margin(self) -> float:
        return 1.0 / self.over + 1.0 / self.under - 1.0
