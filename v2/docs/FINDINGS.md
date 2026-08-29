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

## 2026-08-29 — Total cards: two new models, and the settlement bug that faked an edge

Asked for two new models for total yellow cards. Built both, and in the process
found the error that has quietly distorted every cards result in this project.

### The two models

**A — team convolution (generative).** Fits each team's card count separately
(own indiscipline; opponent's card-drawing) and composes the total, taking
dispersion from residuals. Rationale: a dirty side against a clean one carries
information that collapses when you only model the sum.

**B — per-line gradient boosting (discriminative).** Predicts P(total > L)
directly per line, no count distribution to be wrong about, and can find
interactions a log-linear model cannot — referees plausibly have thresholds,
not slopes.

First attempt at B fitted per league and was badly overconfident (log-loss
0.72–0.79 against the GLM's 0.56–0.67). ~1,400 rows per league is far too
little for trees. **Pooling across leagues with league as a feature fixed it**
and made B the best model we have.

Out-of-sample (train ≤2024-06-30, test n=2,784), mean Brier across four lines:

| model | Brier | vs base rate |
|---|---|---|
| league base rate | 0.2190 | — |
| league Poisson | 0.2222 | −1.5% |
| glm (existing) | 0.2131 | +2.7% |
| A team convolution | 0.2161 | +1.3% |
| **B pooled GBM** | **0.2105** | **+3.84%** |

### The settlement bug

Against 149 real Pinnacle cards quotes, B first appeared to have a genuine
edge — and, uniquely in this project, ROI **rose** with the edge threshold
(+2.0% → +3.5% → +3.8%), the pattern Stage 0 identified as the fingerprint of
a real edge rather than noise.

It was not real. Diagnostics:

- 75% of the model's bets were **Under**, returning +9.5%; the 25% Overs
  returned −20.8%.
- The market's mean P(over) was **48.1%** while the actual over-rate was
  **37.6%** — a 2.6σ miss by the sharpest book in football, which is not
  credible.
- Card rates were checked and are **stable** (3.80–4.08 per month across the
  whole window), so this was not a low-card period.
- The sample was representative (3.97 vs 3.94 cards in the population).

The cause was our own settlement. Testing three definitions against those 149
quotes:

| card definition | mean | market says | actual | gap |
|---|---|---|---|---|
| yellows only *(what we used)* | 3.97 | 48.1% | 37.6% | **−10.5%** (2.6σ) |
| yellows + reds | 4.18 | 48.1% | 41.6% | −6.5% (1.6σ) |
| **yellows + 2×reds** | 4.40 | 48.1% | 44.3% | **−3.8%** (0.9σ) |

**Books settle a red as two cards** — a second yellow is yellow + red, and a
straight red counts double. We were modelling and grading yellows alone, which
undercounts every match and made every Under look cheap.

### After the correction

| model | c | Brier vs market | ROI @0% / @2% / @5% | gate |
|---|---|---|---|---|
| B pooled GBM | −0.109 (p=0.77) | 0.2585 vs **0.2476** | −5.1% / −5.1% / −9.6% | **FAIL** |
| glm | −0.347 | 0.2774 vs 0.2482 | −10.8% → −12.3% | FAIL |
| A team convolution | −0.339 | 0.2924 vs 0.2482 | −14.0% → −10.8% | FAIL |

The market becomes well calibrated (48.1% vs 44.3%) and the edge inverts to
−5%. **The entire apparent edge was our own accounting error.**

This retroactively invalidates every cards number in this project, v1's yellow-
card thesis included. The corrected league mean is **4.48 cards**, not 4.12.

**Model B is still the best predictor we have (+3.84% over base rate) and it
still loses to Pinnacle.** Being better than a naive baseline and being better
than the market are different achievements, and only the second one pays.

## 2026-08-29 — 1win prices YELLOWS; Pinnacle prices CARDS. This explains everything.

Four more 1win screenshots, all La Liga, prompted the check that closes the loop.

1win's market is labelled **"Yellow cards. Total"** and the app carries a
*separate* "Will a red card be shown?" market. Pinnacle's `totals_cards`
settles a red as two (established 2026-08-29 above). **These are different
quantities**, and the difference is large in exactly the leagues we care about:

| league | reds/match | yellows | book-rule | gap |
|---|---|---|---|---|
| **La Liga** | 0.249 | 4.71 | 5.21 | **0.50** |
| Serie A | 0.191 | 4.24 | 4.62 | 0.38 |
| Bundesliga | 0.143 | 3.85 | 4.13 | 0.28 |
| EPL | 0.116 | 3.64 | 3.87 | 0.23 |

### It reverses the La Liga "value"

1win pays more than Pinnacle on every line — which looks like an edge until you
price the right quantity:

| match | line | 1win | EV vs Pinnacle's *cards* | EV vs *yellows* |
|---|---|---|---|---|
| Sevilla v Atlético | 3.5 | 1.43 | +0.0% | **−12.0%** |
| Sevilla v Atlético | 4.5 | 2.02 | +4.3% | **−14.8%** |
| Real Sociedad v Espanyol | 3.5 | 1.36 | +3.2% | **−6.2%** |
| Real Sociedad v Espanyol | 4.5 | 1.85 | +10.1% | **−5.6%** |

1win's longer prices are not generosity. They are the correct price for a
**less likely event**.

### It explains the losing bet

On 2026-08-22 I observed that 1win's implied probabilities sat consistently
*below* Pinnacle's, by 0.7–5.9%, and wrote that this made Over "systematically
the better side at 1win". The red-card adjustment alone predicts a gap of
**+3.4%**; the observed mean gap was **+3.3%**.

The signal was the market definition, not a pricing bias. Acting on it, I
recommended Hull City Over 4.5 at 2.50 — pricing a *yellows* market off a
*cards* distribution. It lost, and all four Over 3.5 bets at 1win would also
have lost. That was not variance; it was a systematic error with a predictable
sign.

### Consequences

1. **To bet 1win, model yellows only.** Pinnacle's cards market is not a valid
   benchmark without subtracting ~2x the expected reds — and that adjustment is
   twice as large in La Liga as in the EPL.
2. **Every EV figure computed against 1win in this project was biased upward**,
   most in the leagues with the most red cards, which is where v1 exclusively bet.
3. The correct read on "La Liga odds aren't bad": compared like with like they
   are **−5% to −15%**, among the worst we have measured.

## 2026-08-29 — Yellows-only model: built, and it loses to 1win by ~12%

Built the model against the quantity 1win actually settles.

**Predictive accuracy** (train ≤2024-06-30, test n=2,784, four lines):
pooled GBM Brier **0.2066** vs 0.2145 league base rate — **+3.67%**. The best
predictor this project has produced.

**1win's pricing structure, measured on 10 captured quotes:**

- Its implied yellows mean equals Pinnacle's book-rule mean minus 2x the
  league red rate, to within **+0.046 cards (sd 0.084)** across the six matches
  where we hold both books.
- Its two-way margin is **8.4% (sd 0.4)** against Pinnacle's ~5.9%.

So 1win's price is not an independent opinion. It is **Pinnacle's number,
correctly converted to yellows, with 2.5 points more margin.** A price
predictor built on that reproduces the real quotes to **3.0% mean absolute
error** with no bias.

**Result against those prices** (447 match-lines, 149 matches):

| | Brier |
|---|---|
| our model | 0.2048 |
| Pinnacle converted to yellows | **0.1958** |

| threshold | n | ROI | 95% CI |
|---|---|---|---|
| 0% | 309 | −11.5% | [−24.1%, +1.9%] |
| 2% | 288 | −13.7% | [−27.2%, +0.5%] |
| 5% | 236 | −12.1% | [−26.8%, +4.1%] |

**Gate: FAIL.** The arithmetic is simple and was foreseeable: we do not beat
Pinnacle, and 1win is a wider-margin copy of Pinnacle. Betting into a worse
version of a book you cannot beat loses the margin plus your own error.

### On collecting more 1win prices

More quotes would sharpen the estimate of a relationship we have already
measured to within 0.05 cards. They cannot create an edge, because the
bottleneck is not knowledge of 1win's prices — it is that our probabilities are
worse than Pinnacle's. Scraping is therefore the wrong thing to automate next.

### What would actually change the answer

Only information Pinnacle does not have at the time it prices. Two candidates:

1. **Referee assignments**, published 1–2 days before kickoff. The strongest
   known predictor of cards, and absent from our features for three of four
   leagues. Pinnacle almost certainly uses it, so this is more likely to close
   our gap than to open a new one.
2. **Confirmed lineups**, ~1 hour before kickoff, which Pinnacle also prices.

Neither is obviously winnable. The honest read is that the totals market for
cards is efficiently priced and our data contains nothing the market lacks.

## 2026-08-29 — Line shopping: soft book vs sharp line. Also dead.

Tested the one thesis never properly examined, with **no model at all**, on
~8,000 matches from the free archive (vs 149–600 for every previous test).
Bet365 as the soft book, Pinnacle as the sharp reference.

**Negative control passed:** betting Pinnacle's own price against its own fair
value returned −4.06% (1X2) and −3.32% (O/U) — almost exactly the margin,
confirming the de-vig and settlement are sound.

### Held-out seasons (2024/25–2025/26, n=2,892)

| arm | market | ROI at 0% → 5% | gate |
|---|---|---|---|
| A close vs close | 1X2 | −10.7% → **−46.2%** | FAIL |
| A close vs close | O/U 2.5 | −9.8% → −31.2% | FAIL |
| A close vs close | Asian handicap | −10.6% → −15.1% | FAIL |
| **B open vs open (actionable)** | 1X2 | −3.9% → **−36.7%** | FAIL |
| C open vs close *(lookahead)* | 1X2 | −2.4% → +2.1% | flat |
| C open vs close *(lookahead)* | Asian handicap | +3.8% → +2.8% | FAIL |
| Max price anywhere | 1X2 | −5.8% → −11.3% | FAIL |

Every arm declines as more edge is demanded — the noise fingerprint, now
appearing on 2,892 held-out matches rather than 149.

### A lookahead bug I caught in my own design

Arm B was first built as "Bet365 **opening** price vs Pinnacle **closing** fair
value", and on the exploratory set it looked excellent: Asian handicap +3.4% →
+7.6%, rho=+1.00, and bootstrap CIs that **excluded zero at every threshold**
on n=3,761.

It was not a strategy. At the moment you place a bet on an opening price, the
closing line does not exist. Selecting with it is lookahead — the arm was
measuring closing-line value, a diagnostic, not something executable.

Rebuilt to compare prices from the same moment (Bet365 open vs Pinnacle open),
the sample collapsed from 3,762 to 636 and the held-out ROI went to −3.9%.

**That collapse is itself the finding.** Out of ~8,000 matches, Bet365's
opening price beats Pinnacle's simultaneous fair value only ~600 times, and
those occasions are not profitable. Two books priced at the same moment simply
do not diverge enough to bet.

### What survives

Only the labelled-lookahead Arm C shows a small persistent positive on the
Asian handicap (~+3%), which is genuine closing-line value: Bet365's openings
do sit slightly off where the market ends up. Acting on it would require
predicting the close — which is exactly what our models cannot do, having
failed to beat Pinnacle in every market tested.

**Per the pre-registered stopping rule, the line-shopping thesis is finished.**
No re-running with different books, markets or windows.

## 2026-08-29 — Systematic strategies: the favourite-longshot bias is real, and still not profitable

Every earlier test asked "is our probability better than the book's". This one
asks a different question needing **no model**: is some *category* of bet
mispriced? Tested 86 pre-specified rules over 55,697 available bets at
Pinnacle's closing price across 8,676 matches.

### The favourite-longshot bias shows up cleanly

| 1X2 price bucket | n | ROI | win rate |
|---|---|---|---|
| favourite (p≥55%) | 3,137 | **+0.24%** | 68.6% |
| mid-favourite | 3,482 | +0.16% | 48.1% |
| mid | 9,351 | −3.51% | 30.1% |
| underdog | 6,603 | −7.62% | 18.7% |
| longshot (p<12%) | 1,352 | **−11.43%** | 7.8% |

A clean monotone gradient, and the same pattern in over/under (fav −1.31%,
mid-fav −3.62%, mid −6.06%). This is the most documented anomaly in betting
markets and it is plainly present: **bettors overbet longshots, so books shade
them, and the loss scales with the price.**

**Backing short favourites escapes the margin entirely — and stops at
break-even.** +0.24%, 95% CI [−2.35%, +2.64%]. Not profitable.

### Draws are the least-bad 1X2 bet

| rule | n | ROI |
|---|---|---|
| back every draw | 7,975 | **−0.69%** |
| back every away | 7,975 | −5.01% |
| back every home | 7,975 | −6.50% |

Backing every draw all season loses **0.7%** against a ~3.5% margin. Real, and
the closest to fair of any blanket 1X2 rule — but the CI is [−4.39%, +3.30%]
and it does not make money. Serie A draws (+3.20%) and Bundesliga draws
(+2.64%) look better still; both CIs span zero comfortably.

**Not one of the 86 rules had a confidence interval excluding zero on the
positive side.**

### The permutation null, which is the real lesson

Team rules looked spectacular. Best on the held-out seasons: *"oppose Real
Sociedad"* at **+45.4%**, and *"back Aston Villa"* held up across both periods
(+30.7% → +32.4%).

So the sweep was paired with a null: **if the market is exactly right, what
does the best of 79 rules look like by luck alone?**

- best rule under the null: **median +49.2%**, 95th percentile +85.1%
- our best observed rule: **+45.4%**
- it sits at the **40th percentile of pure noise** — worse than the median of luck

And the correlation between a rule's exploratory ROI and its held-out ROI was
**+0.108**, essentially zero. "Back Milan" went +20.3% → −4.2%; "oppose
Everton" +15.6% → −28.9%; "back Wolves" +11.0% → −32.5%.

With ~57 bets per rule, +45% is what the *best of 79 coin-flips* looks like.
This is precisely how betting systems get born, and the null is the only thing
that tells them apart from an edge.

### Practical upshot

If you are going to bet anyway, the market's smallest error is on **short-priced
favourites and draws**, and its largest is on longshots. But favourites at
Pinnacle's closing price return zero, and at 1win's 8.4% margin instead of
Pinnacle's ~3.5% the same bets lose roughly 5%. The bias is real; it is not
large enough to sell.

## 2026-08-29 — Feature mart, rule scan, and why hit rate cannot be bought

Built two tables so rules can be written against columns instead of rebuilt
each time. Everything is as-of before kickoff; rolling stores update only after
a row is emitted, so lookahead is structurally impossible.

- **`team_match_form`** — 17,352 rows (one per team per match): league position,
  season points, last-5/last-10 form, venue-split card/corner/goal averages,
  win/loss/unbeaten/winless streaks, `last5` result string, days rest.
- **`match_features`** — 8,676 rows: round number, season stage, month, the
  v1-style combined predictors (`cards_pred`, `corners_pred`, `goals_pred`,
  `fouls_pred`), position/points/form differentials, and every outcome.

### The rules work

Scanned 5,126 rules (466 filter combinations × 11 outcomes). Unlike the
team-name rules, the top ones **held up out of sample**:

| rule | explore | held out | n |
|---|---|---|---|
| big mismatch + home on 3+ win streak → not away win | 89.4% | **88.9%** | 63 |
| big mismatch + home in form → not away win | 89.2% | **87.2%** | 180 |
| Bundesliga + corners_pred≥11 → goals over 1.5 | 88.4% | 84.1% | 151 |
| home in form + away winless → not away win | 85.6% | **88.8%** | 134 |

Mean change from exploratory to held-out: **+1.1 points**. These are real
football relationships.

### And they are already in the price

| rule | n | hit rate | fair odds | ROI at fair | ROI at 1win's margin |
|---|---|---|---|---|---|
| big mismatch + home win streak≥3 | 185 | **90.3%** | **1.13** | +1.81% | **−6.08%** |
| big mismatch + home in form | 515 | 89.1% | 1.19 | +2.70% | −5.26% |
| home in form + away winless≥3 | 399 | 86.5% | 1.19 | +1.45% | −6.42% |
| *(all matches, baseline)* | 7,975 | 68.6% | 1.59 | −0.95% | −8.63% |

### The relationship that ends the search

Hit rate and price are the same number wearing different clothes:

| market-implied band | n | actual hit rate | average fair odds |
|---|---|---|---|
| 85%+ | 1,409 | 90.8% | **1.11** |
| 75–85% | 1,913 | 81.7% | 1.25 |
| 65–75% | 1,736 | 71.9% | **1.43** |
| 55–65% | 1,265 | 58.1% | **1.67** |
| under 55% | 1,652 | 39.2% | 2.52 |

**Bets priced at 1.50–1.60 hit at 63–67% — exactly break-even — because that is
what 1.50–1.60 means.** Finding a 90% rule is easy and ours are genuinely good;
they are priced at 1.13. The odds cannot be separated from the hit rate,
because the odds *are* the hit rate.

The only visible bright spot is that these short-priced rules return +1.5% to
+2.7% at fair value against the −0.95% baseline — the favourite-longshot bias
again. It does not survive contact with any real margin.

## 2026-08-29 — Cards rules priced against real odds: suggestive, not established

Fetched Pinnacle cards quotes for 346 more La Liga matches (3,717 credits,
100 left), giving **389 La Liga matches with real prices** spanning
2024-10 → 2026-05. Then priced every card rule against them.

| rule | n | Pinnacle market, own price | 1win yellows, derived price |
|---|---|---|---|
| ALL La Liga (baseline) | 355 | 45.9% hit, −9.2% | 63.0% hit, −2.7% |
| cards_pred ≥ 5.0 | 123 | 48.8%, −3.0% | 70.0%, +1.0% |
| both poor form | 63 | 54.0%, +6.0% | 69.7%, +6.0% |
| both bottom 6 | 31 | 51.6%, −1.8% | 71.0%, −3.8% |
| **fouls_pred ≥ 28** | 65 | 50.8%, **+0.8%** | 82.9%, **+16.8%** |

The last row was the only CI excluding zero — and it does not survive scrutiny.

### Why it is not (yet) real

1. **It rests on an assumption that swings it 20 points.** The 1win price is
   derived by subtracting 2x a *constant* league red rate. But foul-heavy
   matches have **fewer** reds, not more: 0.157 against 0.259 for the rest.
   Using the correct rate the result falls to **+11.7%, CI [−2%, +24%]** — no
   longer significant. With no adjustment at all, +4.7%.
2. **It does not appear against observed prices.** On Pinnacle's own market at
   Pinnacle's own quote, the same rule returns **+0.8%**. The apparent edge
   lives entirely in a price we computed rather than one we saw. (Caveat in
   fairness: Pinnacle's market includes reds, which add noise to a rule that
   predicts yellows, so the two are not strictly comparable.)
3. **n=70, and it is one of 22 tests.** One result at p<0.05 out of 22 is what
   chance produces.

### What is genuinely there

Foul-heavy La Liga matches do carry more yellows: **5.09 vs 4.30**, and
82.9% go over 3.5 against a 58.8% base. Pinnacle under-estimates them slightly
more than other matches (implied book mean 5.14 vs actual 5.40, a +0.26 gap,
against +0.14 elsewhere).

That is a real, small, and plausible effect — fouls cause cards, and a
market pricing off card history alone would miss part of it. It is nowhere near
established, and it is worth **one specific, cheap test** rather than a stake.

### The one test that would settle it

Real 1win prices for foul-heavy La Liga matches. Everything above rests on a
derived price; 20-30 observed quotes would replace the assumption that is
currently doing the work. Screenshots are the only way to get them.

## 2026-08-29 — Results ingest, a season-boundary bug, and the watchlist

**Built the job v1 never had working.** `v2/ingest/results.py` pulls finished
matches and their statistics from SofaScore by event id. Data is now current to
today rather than 2026-05-24, and a run writing zero rows is an error.

**Season-boundary bug, found while building the watchlist.** The feature mart
keyed team state by `(league, season, team)`, so *all* rolling history reset
each August. Consequences: `cards_pred` was 0 for the first five rounds of
every season, and every side looked out of form on three games played, so
"both poor form" fired spuriously on early-season matches. Fixed — points and
league position reset each season, rolling card/foul/corner averages carry
across.

Re-ran the priced analysis on the corrected mart. The `fouls_pred >= 28` lead
is essentially unchanged: **82.6% hit, +15.6%, CI [+2%, +29%], n=65** against
+16.8% before. So the bug neither created nor concealed it — and the earlier
verdict stands: it rests on a derived price whose red-rate assumption moves the
answer by 20 points, and it returns only **+0.9%** against Pinnacle's observed
quote.

**Watchlist** (`v2/scripts/watchlist.py`) lists upcoming fixtures clearing the
threshold, so quotes get captured on the matches that matter rather than at
random. Roughly 15-20 qualify per La Liga season, which is also the honest
constraint: this is a thin rule.
