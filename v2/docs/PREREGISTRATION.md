# Pre-registration — cards handicap & corners totals

**Committed before any historical odds were fetched.** Everything below is
fixed in advance. Any deviation must be recorded as a deviation, with a reason,
in `FINDINGS.md`. This document exists because v1 grid-searched 54
configurations and reported the maximum as an expected return.

Git SHA at registration: recorded in the commit that adds this file.

---

## 1. Question

Does information in our data improve on the closing price enough to bet, in
either of two markets we have never tested?

## 2. Markets and hypotheses

**H1 — cards handicap (`alternate_spreads_cards`).** Team-level *fouls* carry
information about how cards distribute between the two teams that the closing
handicap does not price.

*Rationale:* fouls are the causal mechanism behind cards, are present for all
8,676 matches, and were never used by v1. This market has the widest observed
margin (7.1% vs 5.9% on totals), which usually signals less attention. It is
the primary hypothesis.

**H2 — corners total (`alternate_totals_corners`).** Shots and corner-share
carry information about total corners beyond the closing total.

*Rationale:* weaker prior. Our corners model already matches Pinnacle's Brier
score without beating it, so H2 is close to a second control.

**H0 — control, already run.** Over/under 2.5 goals. Result: no exploitable
edge (see `FINDINGS.md`, 2026-08-29). Harness validated in both directions.

## 3. Sample

- Window **2025-12-01 → 2026-05-24** (start set by corners ladder availability).
- All four leagues.
- **600 matches**, stride-sampled by `target_matches()` — reproducible, evenly
  spread, no `ORDER BY random()`.
- Odds snapshot at **kickoff − 3 hours**.
- **No filtering on ladder depth.** Every match with a two-sided quote is used,
  at whatever line the market quotes. The previous cards backtest required ≥2
  lines and thereby selected 25 unrepresentative matches averaging 3.12 cards
  against 4.14 elsewhere.

**Split:** train ≤ 2025-11-30 · calibration = first half of the window ·
**hold-out = final six weeks, read exactly once.**

## 4. Model

- Features from `v2/model/features.py` only. Rolling stores update *after* a row
  is emitted, so lookahead is structurally impossible.
- Cards handicap: `CARD_DIFF_FEATURES` (fouls for/against, cards for/against,
  market 1X2 probabilities), predicting the card **difference**.
- Corners total: `CORNER_FEATURES`, negative binomial via `counts.train`.
- League is a partition, not a feature.
- De-vig with **Shin** (`devig_shin`), two-way, at the quoted line.

## 5. Tests, fixed in advance

**Primary gate — out-of-sample money test with edge monotonicity.**
On the hold-out slice, bet whichever side the blended probability prices as
+EV, at thresholds **{0%, 2%, 5%}**. A market passes only if:

1. ROI at the 2% threshold is **positive**, and
2. ROI is **non-decreasing** across the three thresholds.

Condition 2 is the fingerprint test from Stage 0: noise produces *declining*
ROI as more edge is demanded, because filtering for the largest disagreements
with the market concentrates our own errors.

**Screening statistic (not a gate).** The incremental-information coefficient
`c` from `logit(y) ~ a + b·logit(q) + c·(logit(p) − logit(q))`. On the Stage 0
control this returned c=+0.507, p=0.044 on a market with no exploitable edge.
**`c > 0` alone never justifies a bet.**

**Multiple testing.** Benjamini–Hochberg at FDR 0.10 across both markets.

**Minimum detectable effect.** At n≈600, c ≈ 0.11. Recorded now so a null
result reads as "edges larger than this are ruled out" rather than "we found
nothing".

## 6. Declared in advance

- Blend coefficients are fitted on the **calibration** slice only, never on the
  hold-out.
- One bet per match per market. Reported n is the decorrelated count.
- Bootstrap: 20,000 resamples, seed 7.
- **Sample-integrity check before analysis:** mean cards and corners in the
  fetched sample must be within 0.25 of the full population for the same
  window. If not, the sample is biased and the run is void.

## 7. Translation to money

Any statistical edge is then priced at 1win's **observed 8.8% margin**
(measured 2026-08-22, n=4 fixtures). An edge that does not clear the margin is
a null result for betting purposes and will be reported as such.

## 8. Stopping rule

If neither market passes the primary gate, the model-driven approach is
finished and will be written up as such. No re-running with different features,
windows or thresholds in search of a pass. Any follow-up is a **new**
pre-registration, labelled exploratory.
