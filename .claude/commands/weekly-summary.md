Show the last N weeks of betting recommendations with actual results.

Steps:
1. If the user specified a number (e.g. "20"), use that many weeks. Default is 20.
2. Run this analysis inline using the existing CSVs:
   - Accumulator: data/reports/accumulator_backtest_v5.csv
   - Draw singles: data/reports/draw_singles_v5.csv
3. Apply current thresholds:
   - Accumulator: min confidence 13 on weakest leg
   - Draw: draw confidence > 0 (gates already applied in CSV)
4. For each week show one row:
   - Week dates
   - What to bet: "ACCUM + DRAW", "ACCUM only", "DRAW only", or "— SKIP —"
   - Result: ✅/❌ with odds for each bet placed
   - Payout: what you'd have won

5. Summary at bottom:
   - Weeks bet vs skipped
   - Accumulator win rate
   - Draw win rate

Format as a clean table. Highlight winning weeks clearly.

Context on thresholds:
- Threshold 13 on accumulator weakest leg → ~1 in 4 weeks qualify, 56% win rate
- Draw gate (pd≥0.29, gap≤1) → fires most weeks when there's a qualifying match
- No confidence threshold on draw — the gates are the filter
