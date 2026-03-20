# Backtest & Validation Plan

## Goal
Find the optimal weekly slip structure that maximises EV using backtested evidence.
Current validated baseline: HA accumulator (EV 1.057) + Draw single (EV 1.067).
Goal: determine whether adding YC / BTTS legs improves EV, and at what thresholds.

---

## Assumed Odds (until Phase 2 validates with real data)

| Market         | Assumed odds | Notes                                      |
|----------------|--------------|--------------------------------------------|
| H/A            | Pinnacle     | Already in DB — exact historical values    |
| YC Over 3.5    | 1.50         | 1.60 for Bundesliga                        |
| BTTS + O2.5    | 2.10         | Rough average — varies widely per game     |
| Draw           | Pinnacle     | Already in DB — exact historical values    |

**Important:** BTTS and YC odds vary significantly game-to-game on 1win.
All EV calculations in Phase 1 are directional only — Phase 2 will calibrate with real odds.

---

## Phase 1 — Backtest with assumed odds (FREE, do first)

### What to build
A simulation script (`src/recommend/backtest_slip_combos.py`) that:
1. Loads full matches_history from DB
2. For each historical week (Monday–Sunday), computes all candidate picks:
   - H/A picks (using existing scorer logic)
   - YC picks (using rolling home_yellow/away_yellow averages)
   - BTTS picks (using rolling home_goals/away_goals averages)
3. Simulates all slip combinations:
   - `HA` (baseline — already validated)
   - `HA + YC` (best YC pick)
   - `HA + BTTS`
   - `HA + YC + YC` (top 2 YC picks)
   - `HA + YC + BTTS`
4. For each combination × threshold grid, records:
   - Weeks qualifying, win rate, avg odds, EV

### Threshold grid to test
- YC: yc_pred ∈ {3.5, 4.0, 4.5, 5.0, 5.5, 6.0}
- BTTS: home_gf5 ∈ {1.5, 1.8, 2.0} × away_gf5 ∈ {1.3, 1.5, 1.8}
- HA min_conf: 13 (fixed, already validated)

### Success criteria
- EV > 1.05 (beats baseline)
- At least 20 qualifying weeks (practical frequency)
- Win rate > 45%

### Output
Table of all combinations sorted by EV, saved to `data/reports/slip_combo_backtest.csv`

---

## Phase 2 — Validate odds assumptions (Odds Portal scraper)

### What to build
A scraper (`src/data/fetch_oddsportal.py`) that:
1. For a given match (home, away, date), fetches historical YC Over 3.5 and BTTS odds from Odds Portal
2. Stores in DB table `market_odds_history` (match, date, market, bookmaker, odds)
3. Target bookmakers: 1win (primary), Pinnacle (reference)

### Why needed
- Assumed 1.50 for YC may be off by 0.2–0.3 which drastically changes EV
- BTTS 2.10 assumption is rough — actual range is probably 1.80–2.50
- Without real odds, Phase 1 conclusions may be directionally right but numerically wrong

### Plan
- Start with ~50 historical matches we've already picked (from weekly_picks table)
- Expand to full history if signal looks strong

---

## Phase 3 — Live odds fetching before each weekly email

### What to build
Before generating picks each Saturday:
1. For each YC/BTTS candidate, fetch current 1win odds from Odds Portal (or BetsAPI)
2. Compute real EV = estimated_prob × actual_odds − 1
3. Only include pick if EV > 0.05
4. Show actual odds in email (not assumed)

### Gating logic (provisional, to be confirmed by Phase 1)
```python
YC_MIN_PRED     = 5.0   # will be set by Phase 1 results
YC_MIN_EV       = 0.05  # only recommend if EV > 5%
BTTS_MIN_HOME   = 1.8
BTTS_MIN_AWAY   = 1.5
BTTS_MIN_EV     = 0.05
```

---

## Current Status

- [x] H/A accumulator backtested and validated (V5, EV 1.057)
- [x] Draw singles backtested and validated (EV 1.067)
- [x] YC calibration run: hit rate by yc_pred bin (see results below)
- [ ] Phase 1: full slip combo backtest
- [ ] Phase 2: Odds Portal scraper
- [ ] Phase 3: live odds in weekly email

---

## YC Calibration Results (from matches_history, all leagues)

| yc_pred range | Games | Hit rate | EV @ 1.50 | EV @ 1.60 |
|---------------|-------|----------|-----------|-----------|
| 3.5 – 4.0     | 1962  | 57.5%    | **-0.14** | -0.08     |
| 4.0 – 4.5     | 1088  | 60.0%    | **-0.10** | -0.04     |
| 4.5 – 5.0     | 1301  | 66.7%    | +0.001    | +0.07     |
| 5.0 – 5.5     |  605  | 65.8%    | -0.01     | +0.05     |
| 5.5 – 6.0     |  476  | 67.0%    | +0.005    | +0.07     |
| 6.0+          |  386  | 72.8%    | **+0.09** | **+0.17** |

**Key finding:** Current threshold of 3.5 is unprofitable at 1.50 odds.
Minimum viable threshold: 4.5 (break-even). High-confidence threshold: 6.0+ (EV +9%).

---

## BTTS Calibration
*To be computed in Phase 1.*

---

## Notes & Decisions

- BTTS and YC odds vary per game on 1win — assumed odds are directional proxies only
- HA+YC+YC (3-leg slip) only makes sense if both YC legs have yc_pred ≥ 6 (high confidence)
- Do not add speculative legs just to increase odds — each leg must have positive standalone EV
- Odds Portal is preferred over BetsAPI for Phase 2 (free vs ~$30/month)
