Run the accumulator backtest for a given model version and show results.

Steps:
1. Run: `python -c "from src.recommend.accumulator import run_backtest; s = run_backtest('$ARGUMENTS')"`
   - If no version specified, default to 'v5'
   - Valid versions: v1, v2, v3, v4, v5
2. Show a summary table comparing all versions run so far (read data/reports/VERSIONS.md)
3. Report for the requested version:
   - Overall win rate, avg odds, EV
   - Per pick type (H/D/A): count, accuracy, avg odds, EV
   - By season breakdown
   - CSV saved location

Key context:
- V5 is the current best version (multi-league: EPL + Bundesliga + Serie A + La Liga)
- EV > 1.0 means profitable long-term
- Accumulator wins only when ALL legs are correct
- Draw singles are separate (see draw_singles_v5.csv)
