"""Does our model carry information the closing line does not?

The primary test is an incremental-information regression:

    logit(y) ~ a + b*logit(q) + c*(logit(p) - logit(q))

with y the outcome, q the de-vigged closing probability, p our model's
probability. Under an efficient market b = 1 and c = 0. A significantly
positive c means we know something the closing price does not.

This is used instead of simulated P&L because P&L is hopeless at this sample
size: with ~50 bets a season it cannot separate a 3% edge from zero, while this
can. v1 reported P&L on 27 in-sample weeks and called it an edge.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
import statsmodels.api as sm

EPS = 1e-6


def logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


@dataclass
class Result:
    market: str
    n: int
    c: float                 # incremental-information coefficient
    c_se: float
    c_p: float
    b: float                 # coefficient on the market's own logit
    brier_model: float
    brier_market: float
    brier_diff_ci: Tuple[float, float]
    base_rate: float
    q_p: Optional[float] = None   # FDR-adjusted p

    @property
    def beats_market(self) -> bool:
        return self.c > 0 and (self.q_p if self.q_p is not None else self.c_p) < 0.05

    def line(self) -> str:
        star = "  <-- BEATS MARKET" if self.beats_market else ""
        q = f" q={self.q_p:.3f}" if self.q_p is not None else ""
        return (f"  {self.market:26s} n={self.n:4d}  c={self.c:+6.3f} "
                f"(se {self.c_se:.3f}, p={self.c_p:.3f}{q})  "
                f"brier {self.brier_model:.4f} vs {self.brier_market:.4f}  "
                f"diff CI [{self.brier_diff_ci[0]:+.4f}, {self.brier_diff_ci[1]:+.4f}]{star}")


def incremental_information(
    y: Sequence[int], q: Sequence[float], p: Sequence[float], market: str
) -> Result:
    y = np.asarray(y, dtype=float)
    lq = np.array([logit(x) for x in q])
    lp = np.array([logit(x) for x in p])
    X = sm.add_constant(np.column_stack([lq, lp - lq]), has_constant="add")
    fit = sm.Logit(y, X).fit(disp=0)

    bm = float(np.mean((np.asarray(p) - y) ** 2))
    bq = float(np.mean((np.asarray(q) - y) ** 2))
    lo, hi = _bootstrap_brier_diff(np.asarray(p), np.asarray(q), y)
    return Result(
        market=market, n=len(y),
        c=float(fit.params[2]), c_se=float(fit.bse[2]), c_p=float(fit.pvalues[2]),
        b=float(fit.params[1]),
        brier_model=bm, brier_market=bq, brier_diff_ci=(lo, hi),
        base_rate=float(np.mean(y)),
    )


def _bootstrap_brier_diff(p, q, y, n_boot: int = 20000, seed: int = 7):
    """Paired bootstrap on (model Brier - market Brier). Negative favours us."""
    rng = random.Random(seed)
    d = (p - y) ** 2 - (q - y) ** 2
    n = len(d)
    idx = range(n)
    sims = []
    for _ in range(n_boot):
        s = [d[rng.randrange(n)] for _ in idx]
        sims.append(sum(s) / n)
    sims.sort()
    return sims[int(0.025 * n_boot)], sims[int(0.975 * n_boot)]


def benjamini_hochberg(results: List[Result], alpha: float = 0.10) -> List[Result]:
    """Control the false discovery rate across all tests in one family.

    Without this, testing enough markets guarantees a winner by chance - which
    is precisely how v1's 54-configuration grid search produced its headline.
    """
    ordered = sorted(results, key=lambda r: r.c_p)
    m = len(ordered)
    for i, r in enumerate(ordered, start=1):
        r.q_p = min(1.0, r.c_p * m / i)
    # enforce monotonicity
    for i in range(m - 2, -1, -1):
        ordered[i].q_p = min(ordered[i].q_p, ordered[i + 1].q_p)
    return ordered


def minimum_detectable_c(n: int, sd_signal: float = 1.0, power: float = 0.80) -> float:
    """Roughly the smallest c we could detect at this n, computed BEFORE
    looking at outcomes so a null result is interpretable."""
    return (1.96 + 0.84) / (math.sqrt(n) * sd_signal)


# ── the gate that actually matters ────────────────────────────────────────

def edge_monotonicity(bets_by_threshold: dict) -> Tuple[bool, str]:
    """A real edge earns MORE as you demand more edge. Noise earns less.

    Discovered in Stage 0: on over/under 2.5 goals - an efficient market where
    no edge should exist - the incremental-information coefficient came back
    c=+0.507, p=0.044. It looked like a finding. But out of sample the money
    test returned -9.6% ROI at a 0% threshold, -17.5% at 2% and -26.6% at 5%.
    Demanding more edge made it worse, monotonically: the fingerprint of a
    model whose disagreements with the market are its own errors.

    The first version of this test only failed when EVERY step declined, which
    passed sequences like -3.6% -> +4.7% -> +0.9% -> +12.2% -> +29.9% that
    plainly wobble. It now requires a non-negative Spearman rank correlation
    between threshold and ROI, and that the top threshold beat the bottom.

    `bets_by_threshold` maps a minimum-edge threshold to (n, roi).
    """
    thresholds = sorted(bets_by_threshold)
    rois = [bets_by_threshold[t][1] for t in thresholds]
    if len(rois) < 3:
        return False, "need at least three populated thresholds"

    def rank(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0] * len(xs)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r

    rt, rr = rank(list(map(float, thresholds))), rank(rois)
    n = len(rt)
    mt, mr = sum(rt) / n, sum(rr) / n
    num = sum((a - mt) * (b - mr) for a, b in zip(rt, rr))
    den = (sum((a - mt) ** 2 for a in rt) * sum((b - mr) ** 2 for b in rr)) ** 0.5
    rho = num / den if den else 0.0

    seq = " -> ".join(f"{r:+.1%}" for r in rois)
    if rho < 0:
        return False, f"ROI trends DOWN as the threshold rises (rho={rho:+.2f}): {seq}"
    if rois[-1] < rois[0]:
        return False, f"top threshold worse than bottom: {seq}"
    return True, f"ROI trends up with the threshold (rho={rho:+.2f}): {seq}"
