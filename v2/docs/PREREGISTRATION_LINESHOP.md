# Pre-registration — soft book vs sharp line

**Committed before any result is computed.** Fixed in advance; deviations get
recorded as deviations in `FINDINGS.md`.

## Question

Does a soft bookmaker systematically offer prices that beat the sharp book's
de-vigged fair value, profitably, after settlement?

This is the one thesis never properly tested. It needs **no model**, so none of
our modelling errors can contaminate it. And the free archive gives n≈8,000
against the 149–600 of every previous test.

## Arms

- **A — same-time.** Bet365 *closing* vs Pinnacle *closing* fair value.
- **B — CLV.** Bet365 *opening* vs Pinnacle *closing* fair value. The actual
  professional strategy: take an early soft price, see if it beat the eventual
  line. Expected to be the stronger arm, because closing prices are the
  sharpest of the day.

Reference: Bet365, chosen because the user bets at 1win only and Bet365 is the
closest large soft book in the archive (8,676 matches). `MaxC` (best price
anywhere) is reported for context but is **not actionable** for a one-book
bettor and will not be claimed as a strategy.

## Markets

| market | sharp reference | soft price | settle from |
|---|---|---|---|
| 1X2 | `pinnacle_close_h/d/a` | `b365_close_*`, `b365_*` | full-time result |
| O/U 2.5 goals | `pinnacle_close_over25/under25` | `b365_close_over25/under25` | total goals |
| Asian handicap | `pinnacle_close_ah_home/away` | `b365_close_ah_home/away` | goal difference vs `ah_close_line` |

## Method

1. De-vig Pinnacle with `devig_shin` (three-way for 1X2) → fair `p`
2. Soft price `b`; `edge = p * b - 1`
3. Bet when `edge >= threshold`; settle with `v2/model/markets.py`
4. One bet per match per market — the decorrelated count is what gets reported

**Threshold grid, fixed now: {0%, 1%, 2%, 3%, 5%}.** Every cell reported,
including losing ones. With no model to fit, threshold-shopping is the main way
to fool ourselves.

## Gates

- **Edge monotonicity** — ROI must not decline as the threshold rises. This
  caught the fake +42% cards result and the fake +3.5% GBM result.
- Bootstrap CI, 20,000 resamples, seed 7, on every ROI.
- Benjamini–Hochberg at FDR 0.10 across all market x arm tests.

**Negative control, run first.** Bet *Pinnacle's own closing price* against
Pinnacle's own fair value. Must return ≈ −(margin/2), roughly −1% to −2%.
Anything else means the de-vig or settlement is broken and nothing else in the
run is interpretable.

**Integrity check.** `max_close_* >= b365_close_*` and `>= pinnacle_close_*` on
every row, by definition of a maximum. A violation means a column mapping error.

## Split

Seasons **2020/21–2023/24** exploratory; **2024/25–2025/26 (~2,900 matches)
held out and read once.**

## Declared in advance

- Subgroups (league, season, outcome) are **exploratory only** and must clear
  monotonicity and FDR. With four leagues and ~30 bets each, a pure-noise null
  produces a best-league ROI of +17% half the time — that is how the earlier
  EPL "+18%" nearly fooled us.
- `MaxC` results are context, never a recommendation, since the user cannot
  access those books.

## Stopping rule

If neither arm passes on the held-out seasons, the line-shopping thesis is
finished and gets written up as such. No re-running with different books,
markets or windows in search of a pass.
