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

## 2026-08-22 — First real 1win prices. The 1.50 does not exist.

Four fixtures captured by hand from the 1win app (`v2/data/onewin_quotes_2026-08-22.json`)
and compared to Pinnacle ladders fitted the same day.

| match | line | Pinnacle P | 1win P | gap | Pinnacle vig | 1win vig |
|---|---|---|---|---|---|---|
| Hull City v Man United | 3.5 | 60.4% | 55.8% | +4.6% | 6.0% | 8.2% |
| Hull City v Man United | 4.5 | 41.5% | 35.5% | +5.9% | 6.0% | 9.0% |
| Nott'm Forest v Leeds | 2.5 | 70.2% | 69.2% | +1.0% | 5.8% | 9.8% |
| Nott'm Forest v Leeds | 3.5 | 49.8% | 49.1% | +0.7% | 5.8% | 8.1% |
| Everton v Crystal Palace | 2.5 | 70.6% | 67.2% | +3.4% | 5.8% | 8.5% |
| Everton v Crystal Palace | 3.5 | 50.3% | 47.1% | +3.2% | 5.8% | 8.4% |
| Ipswich v Sunderland | 2.5 | 70.1% | 66.5% | +3.6% | 5.8% | 9.9% |
| Ipswich v Sunderland | 3.5 | 50.0% | 45.9% | +4.1% | 5.8% | 8.1% |

**Three conclusions.**

1. **v1's central price was fiction.** It hardcoded 1.50 for Over 3.5 cards on
   every pick, for 32 picks. Real 1win prices for that line are **1.67, 1.88,
   1.95, 2.00** — and they vary by fixture, as Pinnacle's do (1.32 to 2.59).
2. **1win is not lazy.** Mean absolute gap to Pinnacle's implied probability is
   **3.3%**, and 1win's prices move with the matchup. The "soft book prices
   flat, sharp book prices each game" thesis is wrong for this market.
   1win's margin is **8.8% against Pinnacle's 5.9%** — worse, but not asleep.
   Only **1 of 16** available bets cleared +2% EV.
3. **But there is a consistent directional bias.** All eight comparisons have
   the same sign: 1win's implied probability is **always below** Pinnacle's, by
   0.7–5.9%. 1win systematically expects fewer cards. That makes **Over** the
   better side at 1win every time, and +EV wherever the gap exceeds roughly half
   the margin (~4.4%). On this sample that is the Hull City fixture only.

This is a testable, narrow thesis and the first one in the project supported by
real prices from the book we actually bet at. n=4 matches, one timestamp — it
needs many more observations before it means anything.

### The favourite leg is what kills accumulators

Priced honestly, with the cards leg at 1win and the favourite at *Pinnacle*
(1win will be worse, so this flatters the slip):

| leg | price | true P | EV |
|---|---|---|---|
| Hull v Man Utd, cards Over 4.5 | 2.50 | 41.5% | **+3.7%** |
| Inter to beat Monza | 1.19 | 80.8% | **−3.8%** |
| **combined** | **2.97** | **33.6%** | **−0.2%** |

On 50 NIS: the cards leg alone returns **+1.87** expected; adding the "easy"
favourite turns it into **−0.09**. Short favourites are the worst-priced bets
on the board, and multiplying legs multiplies margin. Every extra leg has to
carry its own edge or it is a tax on the leg that has one.

## 2026-08-29 — First recommendation settled: LOST

The 2026-08-22 fixtures, resolved from SofaScore.

| match | Pinnacle implied mean | actual cards |
|---|---|---|
| Hull City v Man United | 4.23 | **3** |
| Nott'm Forest v Leeds | 3.81 | **3** |
| Everton v Crystal Palace | 3.74 | **1** |
| Ipswich v Sunderland | 3.68 | **3** |
| **mean** | **3.87** | **2.50** |

**The one bet recommended — Hull City v Man United, cards Over 4.5 @ 2.50 —
lost.** 3 cards. On a 50 NIS stake, −50.

Had we bet Over 3.5 on all four (the v1 strategy, at real 1win prices rather
than the assumed 1.50): **0 for 4, −200 NIS, −100% ROI.**

**The directional signal from 2026-08-22 was contradicted immediately.** Last
week 1win's implied probabilities sat consistently *below* Pinnacle's, and I
noted that made Over the better side there. On all four fixtures 1win was the
one closer to right, and every match landed under both books' expectations.

n=4 on a single matchday. This does not show 1win is sharper than Pinnacle, and
one lost bet is not evidence of anything. But it is exactly the size of sample
the earlier "81.5% hit rate" was built on, which is the point: at this n,
nothing is knowable. The honest record so far is **1 recommendation, 1 loss.**

## Standing state as of 2026-08-29

Built and pushed: schema, ingest (football-data + SofaScore + Odds API),
de-vigger and ladder fit, features, count models, backtest harness, 40 tests.

**Not built: the app, and every scheduled job.** Nothing runs on a timer, so
nothing settles bets, refreshes odds, or checks freshness. The Supabase project
consequently sat idle for seven days and was paused by the free tier — which is
the failure mode v1 died of, arriving early, and the reason `healthcheck` was
meant to be Phase 1 rather than an afterthought.

## 2026-08-29 — Stage 0: the harness gate caught two errors before any credits were spent

**Error 1: I ingested opening odds and called them closing.** `Avg>2.5` and
`P>2.5` in the football-data CSVs are the OPENING prices; `AvgC>2.5` and
`PC>2.5` are the close. The first control run measured "can our model beat the
opening line" — and beating openings is easy and well documented, because lines
sharpen as money arrives. This is the same class of error I caught in v1 for
1X2 (`PSH` vs `PSCH`) and then repeated for totals. Fixed; both are now
ingested and labelled distinctly.

**Error 2: the incremental-information test is too trigger-happy to be a gate.**
Against the true closing line, over/under 2.5 goals — one of the most
efficiently priced markets in football — still returned:

```
c = +0.507  (se 0.252, p = 0.044)      <- looks like a finding
```

It was not. Diagnostics:

| check | result |
|---|---|
| driven by market features in our model? | no — *stronger* without them (c=+0.611) |
| stable across the test period? | **no** — first half p=0.257, second half p=0.083 |
| Brier score | model **worse** (0.2430 vs 0.2421) |
| blend refit on a calibration slice | **c = −0.470**, opposite sign |

And the out-of-sample money test on a held-out slice:

| minimum edge demanded | bets | ROI |
|---|---|---|
| 0% | 198 | **−9.6%** |
| 2% | 127 | **−17.5%** |
| 5% | 47 | **−26.6%** |

**The gate is now the money test plus edge monotonicity.** A real edge earns
*more* as you demand more edge; noise earns less, because filtering for your
biggest disagreements with the market concentrates your own largest errors.
This declining pattern is a fingerprint, it is cheap to compute, and it would
have flagged the fake +42% cards result immediately.

`incremental_information` is demoted to a screening statistic. `c > 0` alone
never justifies a bet.

The control now passes in the sense that matters: **no exploitable edge exists
on goals over/under 2.5**, which is the correct answer, and the harness proved
capable of reaching it. Stage 3 may proceed.
