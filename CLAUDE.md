# Winner Recommender — Project Plan & Status

## What This Is
A Python-based sports betting recommendation engine for the Israeli **Winner** platform.
Finds value bets across EPL, Bundesliga, Serie A, and La Liga.
Outputs two weekly recommendations:
1. **Accumulator** — 2-3 high-confidence H/A picks combined on one slip
2. **Draw single** — 1 standalone draw pick on a separate slip

Target: profitable long-term (EV > 1.0) by only betting when confidence thresholds are met.

---

## Key Decisions Made

### Data Sources
| Source | What we use it for | Notes |
|--------|-------------------|-------|
| football-data.co.uk | Historical match results + Pinnacle/B365 closing odds | Free CSV download, 5+ seasons |
| The Odds API | Current upcoming match odds (pre-match) | Free 500 req/month, refresh weekly |
| SofaScore (RapidAPI) | Match schedule, team form, H2H | Used in old daily_report.py |

**Decision:** Use football-data.co.uk as primary historical source (free, includes Pinnacle closing odds).
The Odds API historical endpoint requires paid plan — we don't use it.

### Leagues Covered
EPL, Bundesliga, Serie A, La Liga — all downloaded from football-data.co.uk (2020–2026).
**Decision:** Build features per-league separately so form stats don't bleed across leagues.

### Model Architecture (V5 — current)

#### Accumulator scorer (H/A only — no draws in accumulator)
Gates:
- Home: `pinnacle_prob_h >= 0.63` + `venue_gap >= 5` + `pts5_diff >= 5` + `home_trend >= 0`
- Away: `pinnacle_prob_a >= 0.58` + `venue_gap <= -5` + `pts5_diff <= -5` + `away_trend >= 0`
- Skip unreliable home teams (Tottenham, Man United, Chelsea, Brighton, West Ham, Bournemouth) unless ph >= 0.72

Score bonuses: win streak (×1.25 for 3+), opponent losing streak (×1.15 for 2+), attacking mismatch (×1.20), form dominance (×1.50 for 12pts vs 3pts)

**Decision:** No draws in accumulator. Draws kill accumulator win rate even when individually profitable.

#### Draw scorer (separate single bet)
Gates: `pinnacle_prob_d >= 0.29` + `|pts5_diff| <= 1` + `home_dr10 > 0.20` + `away_dr10 > 0.20`
Rank by: Pinnacle draw probability (sharpest signal — complex scoring formulas don't help)
Pick: Best 1 draw per week only

**Why these gates:**
- Tested all combinations of pd_min (0.28–0.32) × gap_max (1–4)
- pd≥0.29 + gap≤1 → 112 weeks, 33.9% accuracy, EV 1.067 ← chosen
- pd≥0.32 + gap≤1 → 22 weeks, 45.5% accuracy, EV 1.335 (too rare to be practical)
- Raising pd beyond 0.29 with gap>1 always hurts — odds drop faster than accuracy rises
- Gap is the most important lever: gap≤1 always beats gap≤2,3,4

### Confidence Thresholds
**Decision:** Apply minimum confidence threshold of 13 on the *weakest leg* of the accumulator.
- Tested thresholds 0–15
- Threshold 13: 34 qualifying weeks out of 138, 56% win rate, EV 1.057
- Threshold 10: 102 weeks, 48% win rate, EV 0.983 (worse than no filter)
- Filtering by *strongest* leg doesn't help — it's the weakest leg that kills weeks
- **No confidence threshold on draw** — draw confidence score doesn't predict accuracy (higher conf = worse results). The gates themselves are the filter.

### Features Used
| Feature | How computed | Why |
|---------|-------------|-----|
| `home_pts5` / `away_pts5` | Points in last 5 overall games | Overall form |
| `home_venue_pts5` | Home team's pts in last 5 HOME games | More predictive than overall |
| `away_venue_pts5` | Away team's pts in last 5 AWAY games | More predictive than overall |
| `venue_gap` | home_venue_pts5 − away_venue_pts5 | Primary form signal in V3+ |
| `pts5_diff` | home_pts5 − away_pts5 | Secondary form signal |
| `home_trend` / `away_trend` | pts last 3 minus pts prior 3 | Filters declining teams |
| `home_winstreak` / `away_losestreak` | Consecutive W/L | Bonus multipliers |
| `home_dr10` / `away_dr10` | Draw rate last 10 games | Draw gate + scoring |
| `home_gf5` / `home_ga5` | Goals scored/conceded avg last 5 | Attacking mismatch bonus |
| Pinnacle implied probs | 1/odds, vig-removed | Primary probability signal |

### Model Version History
| Version | Win Rate | Avg Odds | EV | Key change |
|---------|----------|----------|----|------------|
| V1 | 52% | 1.93x | 1.005 | H/A only, basic gates |
| V2 | 46% | 3.55x | 1.630 | Added draws to accumulator |
| V3 | 54.5% | 1.81x | 0.990 | Venue form + trend + streaks, no draws in accum |
| V4 | 54.5% | 1.81x | 0.990 | Draws as separate singles (V3 + draw scorer) |
| V5 | 49.3% | 2.11x | 1.040 | Multi-league (EPL+BL+SA+LL), draw singles improved |

**Why V2 has highest EV:** Draws at 3.35 odds boost the average even with lower win rate. But draws in accumulators make most weeks fail — chosen not to mix them.

### Team Name Mapping
The Odds API uses different names than football-data.co.uk. Full mapping in `src/recommend/recommend_today.py` → `NAME_MAP` dict.
Key mappings: "Brighton and Hove Albion"→"Brighton", "Wolverhampton Wanderers"→"Wolves", "Atlético Madrid"→"Ath Madrid", "Borussia Monchengladbach"→"M'gladbach", "AC Milan"→"Milan", "Inter Milan"→"Inter", "AS Roma"→"Roma", "Atalanta BC"→"Atalanta"

---

## Project Structure

```
winner-recommender/
├── CLAUDE.md                          # This file
├── .claude/commands/                  # Slash commands (skills)
│   ├── recommend.md                   # /project:recommend
│   ├── backtest.md                    # /project:backtest
│   ├── weekly-summary.md              # /project:weekly-summary
│   └── fetch-data.md                  # /project:fetch-data
├── data/
│   ├── db/winner.db                   # SQLite: matches_history + odds_raw
│   ├── raw/football_data/             # Downloaded CSVs from football-data.co.uk
│   └── reports/
│       ├── VERSIONS.md                # Auto-logged backtest results per version
│       ├── accumulator_backtest_v*.csv
│       ├── draw_singles_v*.csv
│       ├── weekly_threshold*.csv
│       └── YYYY-MM-DD_recommendation.md
└── src/
    ├── data/
    │   ├── fetch_football_data.py     # Downloads historical CSVs → DB
    │   └── fetch_odds.py              # Downloads current odds → DB
    ├── recommend/
    │   ├── accumulator.py             # Versioned backtest engine
    │   └── recommend_today.py         # Daily recommendation generator
    └── features/                      # Legacy feature modules (SofaScore-based)
```

---

## Current Thresholds (apply in recommend_today.py)

```python
ACCUM_MIN_CONF   = 13      # min confidence on weakest accumulator leg
ACCUM_MIN_LEGS   = 2       # need at least 2 qualifying legs to bet
DRAW_PD_MIN      = 0.29    # Pinnacle draw prob minimum
DRAW_GAP_MAX     = 1       # max |pts5_diff| for draw pick
DRAW_DR_MIN      = 0.20    # min draw rate for both teams (last 10)
```

---

## Backtest Results (V5, all leagues)

**Accumulator with threshold 13:**
- 34 qualifying weeks out of 138 total (~1 in 4)
- 56% win rate | ~1.89x avg odds | EV 1.057

**Draw singles with pd≥0.29, gap≤1:**
- 112 qualifying weeks | 33.9% accuracy | 3.14x avg odds | EV 1.067

**Both EV > 1.0 — profitable long-term.**

---

## Weekly Workflow

1. **Once a week** (Monday morning recommended):
   ```bash
   cd /Users/idoshveki/projects/winner-recommender
   source .venv/bin/activate
   python src/data/fetch_odds.py          # refresh upcoming odds
   python src/recommend/recommend_today.py # generate picks
   ```
2. Report saved to `data/reports/YYYY-MM-DD_recommendation.md`
3. If accumulator threshold not met → skip that week
4. If draw gate not met → skip draw bet that week

---

## Research Findings
Full market-by-market findings with accuracy stats, thresholds, and verdicts:
→ **`data/reports/FINDINGS.md`**

---

## What NOT to do (lessons learned)

- **Don't mix draws into the accumulator** — they increase EV on paper but cause most weeks to fail
- **Don't use confidence threshold on draws** — the draw confidence score doesn't rank quality, only the gates matter
- **Don't use threshold 10 on accumulator** — worse than no filter
- **Don't filter by strongest leg** — filter by weakest leg
- **Don't use The Odds API historical endpoint** — requires paid plan; use football-data.co.uk instead
- **Don't build features across leagues** — process each league separately to avoid form bleed
- **Don't raise pd_min beyond 0.29 if gap_max > 1** — odds fall faster than accuracy rises
