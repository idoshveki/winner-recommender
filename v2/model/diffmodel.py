"""Model for the cards HANDICAP: the difference in cards between the two teams.

The totals model is no use here. A handicap asks which side collects more
cards, so we model each team's count separately and take the difference.

With home cards ~ Poisson(mu_h) and away cards ~ Poisson(mu_a) independent,
the difference follows a Skellam distribution and P(cover) is exact.

Caveat, stated rather than buried: cards are mildly overdispersed relative to
Poisson (mean 4.20, variance 4.80 on the total), so Skellam will understate the
tails somewhat. It is the right first model - simple, exactly computable, and
honest about its assumption - and the experiment's gate is a money test, which
will punish the approximation if it matters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import statsmodels.api as sm
from scipy.stats import skellam

from v2.model.features import FeatureRow


@dataclass
class LeagueDiffModel:
    league: str
    feature_names: List[str]
    params_home: np.ndarray
    params_away: np.ndarray
    means: np.ndarray
    n_train: int

    def _design(self, rows: Sequence[FeatureRow]) -> np.ndarray:
        X = np.empty((len(rows), len(self.feature_names)))
        for i, r in enumerate(rows):
            for j, name in enumerate(self.feature_names):
                v = r.features.get(name)
                X[i, j] = self.means[j] if v is None else v
        return sm.add_constant(X, has_constant="add")

    def mus(self, row: FeatureRow):
        X = self._design([row])
        return float(np.exp(X @ self.params_home)[0]), float(np.exp(X @ self.params_away)[0])

    def p_cover(self, row: FeatureRow, point: float, side: str = "home") -> float:
        """P(team covers) where a team covers when own + point > opponent.

        For the home side that is P(H - A > -point); the away side is the
        mirror, P(A - H > -point) = P(H - A < point).
        """
        mu_h, mu_a = self.mus(row)
        d = skellam(mu_h, mu_a)
        if side == "home":
            k = -point
            # P(D > k); for a half-integer k there is no push mass
            return float(1.0 - d.cdf(np.floor(k)))
        k = point
        return float(d.cdf(np.ceil(k) - 1))


@dataclass
class DiffModel:
    feature_names: List[str]
    by_league: Dict[str, LeagueDiffModel] = field(default_factory=dict)

    def p_cover(self, row: FeatureRow, point: float, side: str = "home") -> Optional[float]:
        m = self.by_league.get(row.league)
        if m is None or not row.usable():
            return None
        return m.p_cover(row, point, side)


def _matrix(rows, names):
    means = []
    for name in names:
        vals = [r.features[name] for r in rows if r.features.get(name) is not None]
        means.append(float(np.mean(vals)) if vals else 0.0)
    means = np.array(means)
    X = np.empty((len(rows), len(names)))
    for i, r in enumerate(rows):
        for j, name in enumerate(names):
            v = r.features.get(name)
            X[i, j] = means[j] if v is None else v
    return sm.add_constant(X, has_constant="add"), means


def train(rows: Sequence[FeatureRow], feature_names: List[str],
          home_attr: str = "home_yellow", away_attr: str = "away_yellow") -> DiffModel:
    """Fit per-league home/away card models. Targets come from the raw counts
    carried on the row, so the caller must have set them."""
    model = DiffModel(feature_names=feature_names)
    for league in sorted({r.league for r in rows}):
        sub = [r for r in rows
               if r.league == league and r.usable()
               and getattr(r, "raw_home", None) is not None]
        if len(sub) < 200:
            continue
        X, means = _matrix(sub, feature_names)
        yh = np.array([r.raw_home for r in sub], dtype=float)
        ya = np.array([r.raw_away for r in sub], dtype=float)
        fh = sm.GLM(yh, X, family=sm.families.Poisson()).fit()
        fa = sm.GLM(ya, X, family=sm.families.Poisson()).fit()
        model.by_league[league] = LeagueDiffModel(
            league=league, feature_names=feature_names,
            params_home=fh.params, params_away=fa.params,
            means=means, n_train=len(sub),
        )
    return model
