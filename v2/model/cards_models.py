"""Two new models for TOTAL yellow cards, deliberately different in kind.

The existing model (counts.py) fits a Poisson GLM to the match total and reads
line probabilities off a negative binomial. Both models here differ from it and
from each other, so that agreement between them means something.

  MODEL A - team convolution with a match-temperature dispersion.
    Generative. Fits each TEAM's card count separately (home cards and away
    cards get their own GLM), driven by that team's own indiscipline and by how
    many cards its opponent tends to draw out of people. The total's mean is
    the sum; its dispersion is estimated from residuals, which is where the
    shared "how hot was this game" effect lands.
    Why it might beat the total-only model: a match between a dirty side and a
    clean one carries information that collapses when you only ever look at the
    sum.

  MODEL B - direct per-line gradient boosting.
    Discriminative. For each line separately, predicts P(total > L) directly
    with a gradient-boosted tree. No count distribution at all, so no Poisson
    or NB assumption to be wrong about, and it can find interactions a
    log-linear model cannot (e.g. fouls matter more in tight games).
    Why it might beat the GLM: the relationship between fouls and cards is
    plausibly nonlinear - referees have thresholds, not slopes.

Both are fitted per league. The league base rates differ by 14 points
(La Liga 67.2% over 3.5 vs EPL 53.3%), so a pooled fit is miscalibrated for
every league at once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import statsmodels.api as sm
from scipy.stats import nbinom, poisson
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression

from v2.model.features import FeatureRow

import os


def _TARGET() -> str:
    """Which card definition to model.

    target_cards_book  - yellows + 2x reds, how Pinnacle settles
    target_cards       - yellows only, how 1win's "Yellow cards" market settles
    """
    return os.environ.get("CARD_TARGET", "target_cards_book")

# Richer than the existing CARD_FEATURES: adds fouls on both sides of the
# ledger (committed and drawn) plus shots as a proxy for how open the game is.
CARD_TOTAL_FEATURES = [
    "cards_home_for", "cards_home_against", "cards_away_for", "cards_away_against",
    "fouls_home_for", "fouls_home_against", "fouls_away_for", "fouls_away_against",
    "cards_league_mean", "fouls_league_mean",
    "shots_home_for", "shots_away_for",
    "mkt_closeness", "mkt_p_draw", "mkt_p_home",
]


def _design(rows: Sequence[FeatureRow], names: List[str], means: np.ndarray) -> np.ndarray:
    X = np.empty((len(rows), len(names)))
    for i, r in enumerate(rows):
        for j, name in enumerate(names):
            v = r.features.get(name)
            X[i, j] = means[j] if v is None else v
    return X


def _means(rows: Sequence[FeatureRow], names: List[str]) -> np.ndarray:
    out = []
    for name in names:
        vals = [r.features[name] for r in rows if r.features.get(name) is not None]
        out.append(float(np.mean(vals)) if vals else 0.0)
    return np.array(out)


# ── Model A: team convolution ────────────────────────────────────────────

@dataclass
class LeagueTeamConv:
    league: str
    names: List[str]
    means: np.ndarray
    params_home: np.ndarray
    params_away: np.ndarray
    dispersion: Optional[float]
    n_train: int

    def mu_total(self, row: FeatureRow) -> float:
        X = sm.add_constant(_design([row], self.names, self.means), has_constant="add")
        return float(np.exp(X @ self.params_home)[0] + np.exp(X @ self.params_away)[0])

    def p_over(self, row: FeatureRow, line: float) -> float:
        mu = self.mu_total(row)
        if self.dispersion is None:
            dist = poisson(mu)
        else:
            dist = nbinom(self.dispersion, self.dispersion / (self.dispersion + mu))
        if abs(line - round(line)) < 1e-9:      # integer line: conditional on no push
            k = int(round(line))
            push = dist.pmf(k)
            return float((1 - dist.cdf(k)) / (1 - push)) if push < 1 else 0.5
        return float(1 - dist.cdf(np.floor(line)))


@dataclass
class TeamConvModel:
    names: List[str]
    by_league: Dict[str, LeagueTeamConv] = field(default_factory=dict)

    def p_over(self, row: FeatureRow, line: float) -> Optional[float]:
        m = self.by_league.get(row.league)
        if m is None or not row.usable():
            return None
        return m.p_over(row, line)


def train_team_conv(rows: Sequence[FeatureRow],
                    names: List[str] = None) -> TeamConvModel:
    names = names or CARD_TOTAL_FEATURES
    model = TeamConvModel(names=names)
    for league in sorted({r.league for r in rows}):
        sub = [r for r in rows if r.league == league and r.usable()
               and r.raw_home is not None]
        if len(sub) < 200:
            continue
        means = _means(sub, names)
        X = sm.add_constant(_design(sub, names, means), has_constant="add")
        yh = np.array([r.raw_home for r in sub], dtype=float)
        ya = np.array([r.raw_away for r in sub], dtype=float)
        fh = sm.GLM(yh, X, family=sm.families.Poisson()).fit()
        fa = sm.GLM(ya, X, family=sm.families.Poisson()).fit()
        mu = np.exp(X @ fh.params) + np.exp(X @ fa.params)
        tot = yh + ya
        excess = float(np.mean((tot - mu) ** 2 - mu))
        disp = None
        if excess > 1e-6:
            r_hat = float(np.mean(mu ** 2) / excess)
            if 0.5 < r_hat < 500:
                disp = r_hat
        model.by_league[league] = LeagueTeamConv(
            league, names, means, fh.params, fa.params, disp, len(sub))
    return model


# ── Model B: direct per-line gradient boosting ───────────────────────────

@dataclass
class LeagueLineGBM:
    league: str
    line: float
    names: List[str]
    means: np.ndarray
    clf: HistGradientBoostingClassifier
    calibrator: Optional[IsotonicRegression]
    n_train: int

    leagues: Optional[List[str]] = None

    def p_over(self, row: FeatureRow) -> float:
        X = _design([row], self.names, self.means)
        if self.leagues:
            onehot = np.zeros((1, len(self.leagues)))
            if row.league in self.leagues:
                onehot[0, self.leagues.index(row.league)] = 1.0
            X = np.hstack([X, onehot])
        p = float(self.clf.predict_proba(X)[0, 1])
        if self.calibrator is not None:
            p = float(self.calibrator.predict([p])[0])
        return min(max(p, 1e-4), 1 - 1e-4)


@dataclass
class LineGBMModel:
    names: List[str]
    by_key: Dict[tuple, LeagueLineGBM] = field(default_factory=dict)

    def p_over(self, row: FeatureRow, line: float) -> Optional[float]:
        m = self.by_key.get((row.league, line))
        if m is None or not row.usable():
            return None
        return m.p_over(row)


def train_line_gbm(rows: Sequence[FeatureRow], lines: Sequence[float],
                   names: List[str] = None, calibrate: bool = True,
                   pooled: bool = True) -> LineGBMModel:
    """pooled=True fits ONE model across all leagues with league as a feature.

    Per-league trees were tried first and were badly overconfident (log-loss
    0.72-0.79 against the GLM's 0.56-0.67). With ~1,400 rows and 15 features
    per league, a tree ensemble has far too little data. Pooling quadruples the
    sample; league identity enters as a one-hot column so the base-rate spread
    (La Liga 67.2% over 3.5 vs EPL 53.3%) is still learnable.
    """
    names = names or CARD_TOTAL_FEATURES
    model = LineGBMModel(names=names)
    leagues = sorted({r.league for r in rows})
    groups = [("ALL", rows)] if pooled else [(lg, [r for r in rows if r.league == lg])
                                             for lg in leagues]
    for league, grp in groups:
        sub = [r for r in grp if r.usable() and getattr(r, _TARGET()) is not None]
        if len(sub) < 300:
            continue
        means = _means(sub, names)
        X = _design(sub, names, means)
        if league == "ALL":
            onehot = np.zeros((len(sub), len(leagues)))
            for i, r in enumerate(sub):
                onehot[i, leagues.index(r.league)] = 1.0
            X = np.hstack([X, onehot])
        totals = np.array([getattr(r, _TARGET()) for r in sub])
        # hold out the last 20% within TRAIN for calibration - never test data
        cut = int(len(sub) * 0.8)
        for line in lines:
            y = (totals > line).astype(int)
            if y[:cut].sum() < 20 or (1 - y[:cut]).sum() < 20:
                continue
            clf = HistGradientBoostingClassifier(
                max_iter=200, learning_rate=0.05, max_depth=3,
                min_samples_leaf=40, l2_regularization=1.0, random_state=7)
            clf.fit(X[:cut], y[:cut])
            cal = None
            if calibrate and cut < len(sub) - 30:
                raw = clf.predict_proba(X[cut:])[:, 1]
                cal = IsotonicRegression(out_of_bounds="clip").fit(raw, y[cut:])
            entry = LeagueLineGBM(league, line, names, means, clf, cal, cut)
            entry.leagues = leagues if league == "ALL" else None
            if league == "ALL":
                for lg in leagues:
                    model.by_key[(lg, line)] = entry
            else:
                model.by_key[(league, line)] = entry
    return model
