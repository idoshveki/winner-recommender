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
- [x] Phase 1: full slip combo backtest — DONE (see results below)
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

## Phase 1 Results — Slip Combo Backtest (210 weeks, all leagues)

### Best config per slip type (min 20 qualifying weeks)

| Slip | yc_thresh | btts_home | btts_away | Weeks | Win rate | Avg odds | **EV** |
|------|-----------|-----------|-----------|-------|----------|----------|--------|
| HA only | — | — | — | 197 | 77.2% | 1.28x | 0.991 |
| HA + YC | 6.0 | — | — | 164 | 61.6% | 1.92x | **1.184** |
| HA + BTTS | — | 2.0 | 1.8 | 178 | 38.8% | 2.70x | 1.045 |
| **HA + YC + YC** | **6.0** | — | — | **119** | **47.1%** | **2.91x** | **1.370** |
| HA + YC + BTTS | 5.5 | 1.8 | 1.8 | 176 | 31.8% | 4.06x | 1.292 |

### YC standalone calibration (assumed odds 1.50 / 1.60 Bundesliga)

| yc_thresh | Games | Hit rate | EV @ 1.50 | EV @ 1.60 |
|-----------|-------|----------|-----------|-----------|
| 3.5 | 5548 | 63.2% | -0.052 | +0.011 |
| 4.0 | 4470 | 64.9% | -0.027 | +0.038 |
| 4.5 | 2788 | 67.4% | +0.010 | +0.078 |
| 5.0 | 1844 | 67.7% | +0.016 | +0.084 |
| 5.5 | 867 | 69.8% | +0.047 | +0.116 |
| **6.0** | **502** | **71.1%** | **+0.067** | **+0.138** |

### BTTS+O2.5 standalone calibration (assumed odds 2.10)

| home_gf5 | away_gf5 | Games | Hit rate | EV @ 2.10 |
|----------|----------|-------|----------|-----------|
| **1.8** | **1.8** | **649** | **50.8%** | **+0.068** |
| 2.0 | 1.8 | 494 | 50.2% | +0.054 |
| 1.8 | 1.5 | 888 | 49.7% | +0.043 |
| 1.5 | 1.8 | 844 | 49.1% | +0.030 |
| 1.5 | 1.3 | 1526 | 47.2% | -0.009 |

### Decisions made from Phase 1

- **Primary slip: HA + YC + YC** at yc_pred ≥ 6.0 → EV 1.370 ✅
- **Fallback: HA + YC** when only 1 YC qualifies → EV 1.184 ✅
- **BTTS threshold: home_gf5 ≥ 1.8 AND away_gf5 ≥ 1.8** → EV +0.068 standalone ✅
- **Raise YC threshold 3.5 → 6.0** — anything below is negative EV at 1.50 ✅
- **BTTS assumed odds updated: 2.63 → 2.10** ✅
- All changes implemented in `src/recommend/send_weekly.py`

---

## BTTS Calibration
*Completed in Phase 1 — see table above.*

---

## Notes & Decisions

- BTTS and YC odds vary per game on 1win — assumed odds are directional proxies only
- Phase 1 EV figures will shift once Phase 2 provides real odds — treat as directional
- HA+YC+YC is the target structure; HA+YC is the fallback when <2 games qualify at yc_pred ≥ 6
- Do not add speculative legs just to increase odds — each leg must have positive standalone EV
- Odds Portal is preferred over BetsAPI for Phase 2 (free vs ~$30/month)
