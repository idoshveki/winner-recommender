Create and test a new model version for the winner-recommender.

When the user wants to try new ideas (different gates, features, thresholds), follow this process:

**1. Understand what to change**
Ask the user what they want to experiment with. Common levers:
- Accumulator gates: pinnacle_prob threshold, venue_gap, pts5_diff, trend filter
- Draw gates: pd_min, gap_max, draw rate minimums
- Confidence threshold (currently 13 on weakest leg)
- New features (H2H, referee data, weather, rest days, xG)
- New leagues

**2. Add the new scorer to accumulator.py**
- Add `scorer_vN(row)` function after the last version
- Add `draw_scorer_vN(row)` if changing draw logic
- Register in `SCORERS` and `DRAW_SCORERS` dicts
- Add to `__main__` loop

**3. Run the backtest**
```python
from src.recommend.accumulator import run_backtest
s = run_backtest('vN')
```

**4. Compare to previous versions**
Read data/reports/VERSIONS.md for the logged history.
Key metrics to compare:
- Win rate (target > 52%)
- EV (target > 1.0)
- Weeks covered (more is better for statistical significance)
- Per-season breakdown (is it consistent or just one good year?)

**5. Run the threshold analysis**
Test if a confidence threshold improves the new version:
```python
# Filter by weakest leg confidence
for thresh in [0, 8, 10, 12, 13, 14, 15]:
    sub = df[df['min_conf'] >= thresh]
    print(thresh, len(sub), sub['won'].mean(), sub['combo_odds'].mean())
```

**Current version history:**
- V1: H/A basic | V2: + draws in accum | V3: + venue form/trend/streaks
- V4: draws as singles | V5: multi-league (EPL+BL+SA+LL) ← current
- Thresholds: accum≥13, draw pd≥0.29 gap≤1 dr>0.20
