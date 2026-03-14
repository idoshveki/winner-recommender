# Model Findings & Research Log
*Last updated: 2026-03-13*

---

## Data Available

| Source | Content | Rows |
|--------|---------|------|
| football-data.co.uk | EPL, Bundesliga, Serie A, La Liga (2020–2026) | 7,916 matches |
| The Odds API | Current upcoming odds (4 leagues, next 7 days) | Refreshed weekly |

### Columns in `matches_history`
- **Result**: home_goals, away_goals, result (H/D/A)
- **Half-time**: ht_home_goals, ht_away_goals, ht_result
- **Shots**: home_shots, away_shots, home_shots_ot, away_shots_ot
- **Corners**: home_corners, away_corners
- **Cards**: home_yellow, away_yellow, home_red, away_red
- **Odds**: Pinnacle (h/d/a + probs), Bet365, market avg, O/U 2.5, Asian handicap line

---

## Market 1: 1X2 Accumulator (H/A only)

**Model version: V5**
**File**: `accumulator_backtest_v5.csv`

### Gates (must ALL pass)
| Condition | Home pick | Away pick |
|-----------|-----------|-----------|
| Pinnacle prob | ≥ 0.63 | ≥ 0.58 |
| Venue gap (home-at-home vs away-at-away, last 5) | ≥ +5pts | ≤ -5pts |
| Overall pts5 diff | ≥ +5pts | ≤ -5pts |
| Form trend | ≥ 0 (not declining) | ≥ 0 |

### Score bonuses
- Win streak ≥ 3: ×1.25
- Win streak ≥ 2: ×1.10
- Opponent losing streak ≥ 2: ×1.15
- Attacking mismatch (gf>1.8 + opp ga>1.5): ×1.20
- Form dominance (12pts vs 3pts): ×1.50
- Unreliable home teams (Spurs/ManU/Chelsea/Brighton/West Ham/Bournemouth): skip unless ph≥0.72

### Confidence threshold: 13 (weakest leg)
| Filter | Qualifying weeks | Win rate | EV |
|--------|-----------------|----------|----|
| No filter | 138/138 | 49.3% | 1.040 |
| ≥ 13 (chosen) | 34/138 (~1 in 4) | **56%** | **1.057** |
| ≥ 15 | 16/138 | 56.2% | 1.097 |

**Verdict: ✅ Use with threshold 13. Bet ~1 week in 4.**

### Individual pick accuracy
- H picks: 74.8% | A picks: 78.1%
- The weakest leg is what kills weeks — filter on min confidence of ALL legs

---

## Market 2: Draw Single

**File**: `draw_singles_v5.csv`

### Gates tested (full grid search pd_min × gap_max)
| pd_min | gap_max | Weeks | Accuracy | EV |
|--------|---------|-------|----------|----|
| 0.28 | 1 | 137 | 32.8% | 1.049 |
| **0.29** | **1** | **112** | **33.9%** | **1.067** ← chosen |
| 0.32 | 1 | 22 | 45.5% | 1.335 (too rare) |
| Any | 2+ | varies | drops sharply | < 1.0 |

### Key findings
- **Gap is the most important lever**: gap ≤ 1 always beats gap ≤ 2, 3, 4
- **Raising pd_min beyond 0.29 without tightening gap hurts** — odds fall faster than accuracy rises
- **Confidence threshold on draws doesn't work** — higher score = worse results. The gate IS the filter.
- **Pick 1 best per week** (ranked by Pinnacle draw prob — simplest is best)

### Chosen gates: pd ≥ 0.29, |pts5_diff| ≤ 1, both draw rate > 20%
- 112 weeks | 33.9% accuracy | 3.14x avg odds | **EV 1.067**

**Verdict: ✅ Use. Bet every qualifying week as standalone single.**

---

## Market 3: Over/Under 2.5 Goals

**File**: `ou_backtest.csv`

### Key findings
| Pick | Weeks | Accuracy | Avg odds | EV |
|------|-------|----------|----------|----|
| OVER (po ≥ 0.60) | 173 | 72.8% | 1.39 | 1.013 |
| UNDER (pu ≥ 0.55) | 29 | 58.6% | 1.50 | 0.878 |
| **Combined** | **202** | **70.8%** | **1.41** | **0.995** |

- Threshold doesn't help — model fires every week, accuracy is uniform
- **Fading Pinnacle on O/U doesn't work** (43.7% when our form disagrees with Pinnacle)
- Pinnacle's O/U line is highly efficient — we're essentially just following it
- UNDER picks are EV-negative — skip
- OVER at EV 1.013 is borderline, very tight odds (1.39)

### League baseline over-rates
| League | Over 2.5% |
|--------|-----------|
| Bundesliga | 60% |
| EPL | 56% |
| Serie A | 50% |
| La Liga | 46% |

**Verdict: ⚠️ Marginally useful as extra accumulator leg only when Pinnacle po>0.65 AND both teams form supports it. Do NOT bet as standalone — EV too close to 1.0 and odds too tight.**

---

## Market 4: First Half Result

**File**: `ht_backtest.csv`

### Key calibration from data (7,915 matches)
| Pinnacle FT prob | HT-H rate | HT-D rate | HT-A rate |
|-----------------|-----------|-----------|-----------|
| ph < 40% | 22.1% | 41.8% | 36.2% |
| ph 40–55% | 36.5% | 42.3% | 21.2% |
| ph 55–70% | 48.0% | 37.7% | 14.3% |
| ph > 70% | **55.6%** | 32.9% | 11.4% |

### HT→FT momentum (massive signal)
| HT result | FT same result |
|-----------|----------------|
| HT=H | **76.4%** ends as H |
| HT=A | **68.3%** ends as A |
| HT=D | 36.9% ends as D (weaker) |

### Model gates (Pinnacle FT prob as proxy — no HT-specific odds available)
- **HT Home**: ph ≥ 0.68 + home team HT lead rate ≥ 40% at home
- **HT Away**: pa ≥ 0.62 + away team HT lead rate ≥ 35% away
- **HT Draw**: skip (only 3 picks, 33% accuracy — not reliable)

### Results by confidence threshold
| Min conf | Weeks | Accuracy |
|----------|-------|----------|
| 0 | 199 | 61.8% |
| 7 | 192 | 63.0% |
| **10** | **93** | **66.7%** ← chosen |

**Pick breakdown at threshold 0:**
- HT=H: 171 picks | 61.4% accuracy
- HT=A: 25 picks | 68.0% accuracy

**Verdict: ✅ Strong signal at threshold 10 (66.7%). Use as standalone single or extra slip leg. Only H/A picks — skip HT draws.**

*Note: We don't have HT-specific odds from Winner, so we can't compute EV precisely. Assume Winner HT odds are roughly Pinnacle FT odds × 0.78 calibration factor.*

---

## Market 5: Corners

**File**: `corners_backtest.csv`

### Total corners distribution (avg 9.7/game)
| Bucket | Frequency |
|--------|-----------|
| ≤ 8 | 38.7% |
| 9–11 | 34.0% |
| 12+ | 27.3% |

### Results by sub-market
| Market | Picks | Accuracy | Verdict |
|--------|-------|----------|---------|
| ≤8 total corners | 72 | 48.6% | ⚠️ Marginal |
| **12+ total corners** | **99** | **25.3%** | ❌ Skip — worse than base rate |
| **First corner: Home** | **27** | **77.8%** | ✅ Excellent |
| **First corner: Away** | **10** | **70.0%** | ✅ Excellent |

### First corner model
- Gate: home generates >65% of expected corners (based on rolling venue averages)
- 37 total picks across 208 weeks — very selective
- **77.8% home / 70.0% away accuracy** — best raw accuracy of any market

### Total corners ≤8
- Gate: expected total < 7.5 (both teams low corner generators at their venues)
- 48.6% accuracy — above the 38.7% base rate but weak EV without odds
- Threshold doesn't help (higher conf = worse, not better)

**Verdict:**
- **First corner team: ✅ Use when it fires (rare but very accurate)**
- **Total 12+: ❌ Skip entirely**
- **Total ≤8: ⚠️ Monitor — above base rate but EV depends on Winner's odds**

---

## Combined Weekly Bet Slip Plan

Each week, check markets in this order. Each is a **separate slip**:

| Priority | Market | Bet when | Expected accuracy |
|----------|--------|----------|-------------------|
| 1 | **1X2 Accumulator** | All legs conf ≥ 13 | 56% week win rate |
| 2 | **Draw single** | pd ≥ 0.29, \|gap\| ≤ 1, both DR > 20% | 33.9% |
| 3 | **First half H/A** | conf ≥ 10, skip draws | 66.7% |
| 4 | **First corner team** | home gen >65% of exp corners | 77.8% / 70% |
| 5 | **O/U 2.5 Over** | po ≥ 0.65 + form supports | 72.8% (tight odds) |

### What to skip
- ❌ Total corners 12+ (25% accuracy — terrible)
- ❌ Draws in accumulator (kills win rate)
- ❌ HT Draw picks (33% accuracy)
- ❌ O/U Under (EV negative)
- ❌ O/U as standalone (odds too tight at 1.39)

---

## Open Questions / Next Steps

1. **Winner HT odds** — we need to check actual Winner odds for first half market to compute EV. Currently using FT Pinnacle as proxy.
2. **First corner odds on Winner** — same, need actual odds (Winner shows H/X/A for first corner).
3. **More leagues** — Ligue 1 ready to download, would add more weekly picks.
4. **BTTS (3-way: one/both/neither)** — data available, not yet modelled.
5. **Which half more goals** — data available (ht_home_goals + home_goals), not yet modelled.
6. **Combine into single weekly report** — next step: wire all 5 markets into `recommend_today.py`.
