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
