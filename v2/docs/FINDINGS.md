# v2 measured findings

Everything here was measured, not assumed. Each entry names the date and how to
reproduce it. v1's FINDINGS.md reported in-sample grid-search maxima as expected
returns; nothing in this file may do that.

## 2026-08-21 — Source data is richer than v1 used

Verified against live football-data.co.uk headers for E0/SP1/D1/I1:

| Column | Availability | v1 status |
|---|---|---|
| `HF`/`AF` (fouls committed) | all 4 leagues, all seasons | **never ingested** |
| `Referee` | **EPL only** (col 12) | never ingested |
| `PSCH/PSCD/PSCA` (Pinnacle **closing**) | all 4 leagues | never ingested |
| `PSH/PSD/PSA` (Pinnacle **opening**) | all 4 leagues | ingested and mislabelled `pinnacle_h` |

v1's "vig-free Pinnacle probability" — the core input to its H/A scorer — was
built on **opening** odds throughout (`fetch_football_data.py:103`).

Re-downloading from source recovers **760 matches** the DB never had:
EPL 2020/21 entire (+380) and the back half of 2025/26 (+380, the gap left by
v1's silently-dead ingest job). Training set: 7,916 -> **8,676**.

Pinnacle coverage in 2025/26 is only ~55% (210/380 EPL). football-data's
Pinnacle feed degraded mid-season. Recent matches need a fallback sharp
reference (`avg_*`, B365) or the live Odds API.

## 2026-08-21 — Cards/corners odds DO exist (v1's central assumption was wrong)

The Odds API event endpoint serves `alternate_totals_cards` and
`alternate_totals_corners` with full line ladders:

| Book | Region | Cards | Corners |
|---|---|---|---|
| pinnacle | eu | yes | yes (+ spreads, team totals) |
| betfair_ex_uk | uk | yes (+ lay) | yes (+ lay) |
| livescorebet / virginbet / leovegas | uk | yes | yes |

**1xBet does not quote these markets**, so there is no automatic proxy for 1win
pricing. Hence the "minimum acceptable odds" output design.

## 2026-08-21 — Measured predictive signal (walk-forward, out-of-sample)

Venue-split rolling for/against, 12-match window, shrunk to league mean;
fit on first half, decile-calibrated, tested on the second half.

| Market | Brier vs constant base rate | Top quintile | Bottom quintile |
|---|---|---|---|
| Cards O3.5 | +1.66% | 67.2% | 48.6% |
| Cards O4.5 | +2.01% | 48.2% | 28.9% |
| Corners O9.5 | +1.4% | 59.4% | 43.7% |
| Corners O10.5 | +1.5% | 49.2% | 33.7% |

Real ranking skill, well calibrated, but **small**. Cards separate better than
corners.

### Fouls did NOT help (contrary to expectation)

Adding venue-split rolling fouls committed/drawn, scaled by the global
cards-per-foul rate (0.1721), to the same harness:

| Market | cards only | cards + fouls |
|---|---|---|
| Cards O3.5 | +1.66% | **+1.54%** (worse) |
| Cards O4.5 | +2.01% | **+2.14%** (better) |

Both differences are inside noise. **Caveat: the blend weight was hand-picked
(0.72/0.28), which is exactly the kind of unfitted constant v1 was full of.**
The fair test is a fitted NB GLM that learns the weight. Treat fouls as a
candidate feature to be evaluated in Phase 3, not as an established win.

## Implication for strategy

A 1.5-2% Brier gain over a *base rate* will not beat a 5-8% bookmaker margin.
The book already knows La Liga cards run hot — that is most of where this gain
comes from, and it is the first thing any book prices. Therefore:

**Strategy B (price edge: de-vigged sharp consensus vs 1win) is the primary
mechanism and needs no model at all. Strategy A (model edge) must prove it adds
to B, at a deliberately low blend weight.** Build B first.

## 2026-08-22 — First honest backtest: NO demonstrated edge vs Pinnacle

Historical cards/corners odds **do exist** on The Odds API (10 credits per
market per region per event). Pulled Pinnacle ladders at kickoff minus 3h for
150 matches spread Feb–May 2026, trained on the 8,080 matches before
2026-02-01, and bet at the real captured prices.

### Headline, with error bars (20k bootstrap resamples, one bet per match-market)

| subset | n | ROI | 95% CI | P(ROI ≤ 0) |
|---|---|---|---|---|
| all | 71 | +12.0% | [−11.0%, +35.1%] | 15.0% |
| cards | 15 | +25.6% | [−30.1%, +82.1%] | 18.7% |
| corners | 56 | +8.4% | [−16.7%, +33.3%] | 25.8% |
| **corners excl. May** | 36 | **−3.6%** | [−34.6%, +28.2%] | 58.7% |

**Nothing here is statistically distinguishable from zero.**

### Two traps this backtest walked into, both caught

1. **Selection bias in the cards sample.** Pinnacle's historical archive only
   carries a multi-line cards ladder for May 2026; every other match has a
   single line. My ladder fit requires ≥2 two-sided lines, so the cards
   backtest silently ran on 25 May-only matches. Those matches averaged
   **3.12 cards against 4.14** for the rest of the sample — end-of-season
   fixtures have fewer cards. The "+42% ROI on cards" was a period effect the
   model happened to sit on the right side of, not an edge.
2. **Correlated bets inflating n.** Betting every qualifying line meant one
   match contributed up to 8 "independent" bets. With `--one-per-market` the
   sample drops from 60 to 15 for cards. Always report the decorrelated number.

### The result that actually matters

Unselected calibration across all 143 priced test matches, model vs Pinnacle:

| market | line | model | book | actual | model Brier | book Brier |
|---|---|---|---|---|---|---|
| corners | 8.5 | 61.4% | 62.2% | 61.5% | 0.2298 | 0.2289 |
| corners | 9.5 | 49.4% | 50.4% | 49.7% | 0.2384 | 0.2382 |
| corners | 10.5 | 37.8% | 39.1% | 38.5% | 0.2271 | **0.2297** |
| corners | 11.5 | 27.6% | 28.9% | 30.1% | 0.2066 | 0.2042 |

The model is **as accurate as Pinnacle and no better** — it wins on one line of
four, by 0.003. That is a respectable result for a from-scratch model and a
fatal one for the strategy: matching a sharp book and then paying its 5.7–6.3%
margin is a guaranteed slow loss.

### Consequence for the design

This confirms the plan's central bet and kills the alternative:

* **Strategy A (model edge vs a sharp book) does not work.** Demonstrated, not
  assumed. Do not stake money on it.
* **Strategy B (price difference between a sharp book and a soft one) remains
  untested**, because we have no 1win prices — historical or live.

The simplifying assumption "1win prices = Pinnacle prices" removes the only
source of edge this project has. Under that assumption the correct expected
return is negative and roughly equal to the margin. **The entire thesis rests
on 1win being materially different from Pinnacle, and that is now the single
most important unknown.**
