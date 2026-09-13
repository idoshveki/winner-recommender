"""Removing bookmaker margin, and fitting a distribution to a whole line ladder.

Two things happen here.

1. De-vig a single over/under pair into a fair probability.
2. Fit a count distribution to ALL of a book's lines at once, which gives the
   book's implied probability at *any* line - including lines it does not quote
   but 1win does. That is the answer to "the model likes Over 3.5 but 1win only
   offers Over 4.5".

Integer lines push. "Over 3.0" wins on 4+, LOSES on 0-2, and returns the stake
on exactly 3. Fair pricing makes o_over = (1 - P(X=L)) / P(X>L), so the implied
value of 1/o_over is the probability CONDITIONAL on no push, not P(X>L). Both
implied values still sum to 1 at fair prices, so the margin is measured the
same way - but reading 1/o_over as P(X>L) understates every integer line.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from scipy.optimize import least_squares
from scipy.stats import nbinom, poisson


# ── single-line de-vigging ────────────────────────────────────────────────

def overround(price_over: float, price_under: float) -> float:
    return 1.0 / price_over + 1.0 / price_under - 1.0


def devig_proportional(price_over: float, price_under: float) -> float:
    """Normalise both implied probabilities so they sum to 1.

    Simple and stable, but it removes margin evenly, which over-prices
    favourites - books load proportionally more margin onto longshots.
    """
    q_o, q_u = 1.0 / price_over, 1.0 / price_under
    return q_o / (q_o + q_u)


def devig_shin(price_over: float, price_under: float) -> float:
    """Shin (1993): assumes margin arises from a share `z` of insider money.

    Corrects the favourite-longshot bias that proportional de-vigging leaves
    behind. Solved by bisection on z.
    """
    q = [1.0 / price_over, 1.0 / price_under]
    booksum = sum(q)
    if booksum <= 1.0:
        return q[0] / booksum

    def implied(z: float) -> List[float]:
        return [
            (math.sqrt(z * z + 4.0 * (1.0 - z) * (qi * qi) / booksum) - z)
            / (2.0 * (1.0 - z))
            for qi in q
        ]

    lo, hi = 0.0, 0.9
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if sum(implied(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    return implied((lo + hi) / 2.0)[0]


def devig_power(price_over: float, price_under: float) -> float:
    """Find k such that sum(q_i ** k) == 1."""
    q = [1.0 / price_over, 1.0 / price_under]
    lo, hi = 0.5, 3.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if sum(qi ** mid for qi in q) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    return q[0] ** k


DEVIG_METHODS = {
    "proportional": devig_proportional,
    "shin": devig_shin,
    "power": devig_power,
}


# ── ladder fitting ────────────────────────────────────────────────────────

def p_over_from_dist(mu: float, r: Optional[float], line: float) -> float:
    """P(total > line), push-aware.

    r is the negative-binomial dispersion; None means Poisson.
    For an integer line the result is conditional on no push, matching what a
    fair price implies.
    """
    if r is None:
        dist = poisson(mu)
    else:
        p = r / (r + mu)
        dist = nbinom(r, p)

    if abs(line - round(line)) < 1e-9:          # integer line: push possible
        k = int(round(line))
        p_push = dist.pmf(k)
        p_over = 1.0 - dist.cdf(k)
        denom = 1.0 - p_push
        return p_over / denom if denom > 1e-12 else 0.5
    return 1.0 - dist.cdf(math.floor(line))


@dataclass
class LadderFit:
    mu: float
    dispersion: Optional[float]      # None => Poisson
    rmse: float
    n_lines: int
    method: str

    def p_over(self, line: float) -> float:
        return p_over_from_dist(self.mu, self.dispersion, line)

    def fair_odds(self, line: float, side: str = "Over") -> float:
        p = self.p_over(line)
        if side.lower() == "under":
            p = 1.0 - p
        return float("inf") if p <= 1e-9 else 1.0 / p


def fit_ladder(
    points: Sequence[Tuple[float, float]],
    *,
    family: str = "nbinom",
) -> LadderFit:
    """Fit a count distribution to (line, p_over) points.

    Fitting the whole ladder at once is far more stable than trusting any
    single line pair, and it extrapolates to lines the book does not quote.
    """
    if len(points) < 2:
        raise ValueError("need at least two lines to fit a ladder")
    lines = [float(l) for l, _ in points]
    targets = [float(p) for _, p in points]
    mu0 = max(sum(lines) / len(lines), 0.5)

    if family == "poisson" or len(points) < 3:
        def resid_p(theta):
            mu = math.exp(theta[0])
            return [p_over_from_dist(mu, None, l) - t for l, t in zip(lines, targets)]

        sol = least_squares(resid_p, [math.log(mu0)], method="lm")
        mu = math.exp(sol.x[0])
        rmse = math.sqrt(sum(r * r for r in sol.fun) / len(sol.fun))
        return LadderFit(mu, None, rmse, len(points), "poisson")

    def resid(theta):
        mu = math.exp(theta[0])
        r = math.exp(theta[1])
        return [p_over_from_dist(mu, r, l) - t for l, t in zip(lines, targets)]

    sol = least_squares(resid, [math.log(mu0), math.log(20.0)], method="lm")
    mu, r = math.exp(sol.x[0]), math.exp(sol.x[1])
    rmse = math.sqrt(sum(x * x for x in sol.fun) / len(sol.fun))
    if r > 500:                       # indistinguishable from Poisson
        return LadderFit(mu, None, rmse, len(points), "poisson")
    return LadderFit(mu, r, rmse, len(points), "nbinom")


def ladder_from_outcomes(
    outcomes: Sequence[dict], *, method: str = "shin"
) -> List[Tuple[float, float]]:
    """Odds-API outcomes -> [(line, fair p_over)], using only two-sided lines."""
    devig = DEVIG_METHODS[method]
    by_line: Dict[float, Dict[str, float]] = {}
    for o in outcomes:
        point = o.get("point")
        if point is None:
            continue
        by_line.setdefault(float(point), {})[o["name"].lower()] = float(o["price"])
    out = []
    for line, sides in sorted(by_line.items()):
        if "over" in sides and "under" in sides:
            out.append((line, devig(sides["over"], sides["under"])))
    return out
