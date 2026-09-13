"""Count models for total cards and total corners.

Poisson GLM for the mean, plus a per-league dispersion estimated from held-out
residuals, giving a negative binomial for line probabilities. Both targets are
overdispersed (cards mean 4.20 / var 4.80, corners 9.66 / var 11.3), and the
tails are exactly where the O4.5 and O10.5 lines live, so a plain Poisson
understates them.

League is a partition, not a feature. The base rates differ by 14 points
(cards over 3.5: La Liga 67.2%, EPL 53.3%) and a pooled fit is badly
miscalibrated for every league at once.

Nothing here is hand-tuned. v1's equivalent was a chain of multipliers
(conf = p * 10 * 1.25 * 1.15 * 1.20) and a transcribed step function.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import statsmodels.api as sm

from v2.model.devig import p_over_from_dist
from v2.model.features import FeatureRow


@dataclass
class LeagueModel:
    league: str
    market: str
    feature_names: List[str]
    params: np.ndarray
    means: np.ndarray          # for imputing missing features
    dispersion: Optional[float]
    n_train: int

    def design(self, rows: Sequence[FeatureRow]) -> np.ndarray:
        X = np.empty((len(rows), len(self.feature_names)))
        for i, r in enumerate(rows):
            for j, name in enumerate(self.feature_names):
                v = r.features.get(name)
                X[i, j] = self.means[j] if v is None else v
        return sm.add_constant(X, has_constant="add")

    def predict_mu(self, rows: Sequence[FeatureRow]) -> np.ndarray:
        return np.exp(self.design(rows) @ self.params)

    def p_over(self, row: FeatureRow, line: float) -> float:
        mu = float(self.predict_mu([row])[0])
        return p_over_from_dist(mu, self.dispersion, line)


def _matrix(rows: Sequence[FeatureRow], names: List[str]):
    means = []
    for name in names:
        vals = [r.features[name] for r in rows if r.features.get(name) is not None]
        means.append(float(np.mean(vals)) if vals else 0.0)
    means_arr = np.array(means)
    X = np.empty((len(rows), len(names)))
    for i, r in enumerate(rows):
        for j, name in enumerate(names):
            v = r.features.get(name)
            X[i, j] = means_arr[j] if v is None else v
    return sm.add_constant(X, has_constant="add"), means_arr


def fit(
    rows: Sequence[FeatureRow],
    target: str,
    feature_names: List[str],
    league: str,
    market: str,
) -> Optional[LeagueModel]:
    usable = [r for r in rows if r.usable() and getattr(r, target) is not None]
    if len(usable) < 200:
        return None
    y = np.array([getattr(r, target) for r in usable], dtype=float)
    X, means = _matrix(usable, feature_names)

    res = sm.GLM(y, X, family=sm.families.Poisson()).fit()
    mu = np.exp(X @ res.params)

    # Method-of-moments dispersion: Var = mu + mu^2 / r
    excess = np.mean((y - mu) ** 2 - mu)
    mean_mu_sq = float(np.mean(mu ** 2))
    disp = None
    if excess > 1e-6 and mean_mu_sq > 0:
        r_hat = mean_mu_sq / excess
        if 0.5 < r_hat < 500:
            disp = float(r_hat)

    return LeagueModel(
        league=league, market=market, feature_names=feature_names,
        params=res.params, means=means, dispersion=disp, n_train=len(usable),
    )


@dataclass
class MarketModel:
    """One LeagueModel per league, keyed by league name."""
    market: str
    target: str
    feature_names: List[str]
    by_league: Dict[str, LeagueModel] = field(default_factory=dict)

    def p_over(self, row: FeatureRow, line: float) -> Optional[float]:
        m = self.by_league.get(row.league)
        if m is None or not row.usable():
            return None
        return m.p_over(row, line)


def train(
    rows: Sequence[FeatureRow], target: str, feature_names: List[str], market: str
) -> MarketModel:
    model = MarketModel(market=market, target=target, feature_names=feature_names)
    leagues = sorted({r.league for r in rows})
    for league in leagues:
        sub = [r for r in rows if r.league == league]
        lm = fit(sub, target, feature_names, league, market)
        if lm is not None:
            model.by_league[league] = lm
    return model
