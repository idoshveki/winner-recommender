Generate today's betting recommendations using the V5 model.

Run the following steps:
1. First refresh the odds by running: `python src/data/fetch_odds.py`
2. Then generate the picks: `python src/recommend/recommend_today.py`
3. Read the output report from `data/reports/` (the file named YYYY-MM-DD_recommendation.md with today's date)
4. Present the recommendations clearly:
   - **ACCUMULATOR**: list each leg with match, pick, odds, and why. State combined odds.
   - **DRAW SINGLE**: match, odds, and why.
   - If no accumulator qualifies (confidence < 13 on weakest leg): say "No accumulator this week — skip"
   - If no draw qualifies (pd < 0.29 or gap > 1): say "No draw pick this week — skip"

Thresholds in effect:
- Accumulator: min confidence 13 on weakest leg, min 2 legs
- Draw: Pinnacle draw prob ≥ 0.29, |form gap| ≤ 1, both teams draw rate > 20%
