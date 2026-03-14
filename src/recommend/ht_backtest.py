"""
First Half Result backtest — Winner market: "תוצאת מחצית ראשונה" (1X2 at half-time).

Key insight from data exploration:
  - HT=H → FT=H: 76.4% (massive momentum signal)
  - HT=A → FT=A: 68.3%
  - Strong favourites (ph>70%) lead at HT 55.6% of the time
  - Draw at HT is most common (40.3%) but least predictable

Since Winner shows HT result market BEFORE the game, we predict HT result.
We use Pinnacle FT odds as a proxy (no HT-specific odds in our data).
Calibrated from historical HT hit rates by Pinnacle prob bucket.

Pick 1 best HT bet per week.
"""

import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

ROOT       = Path(__file__).resolve().parents[2]
DB_PATH    = ROOT / "data" / "db" / "winner.db"
REPORT_DIR = ROOT / "data" / "reports"


def build_ht_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add rolling half-time form features per team."""
    team_home = {}   # home team's home games HT stats
    team_away = {}   # away team's away games HT stats

    rows_out = []
    for _, row in df.iterrows():
        ht, at = row['home_team'], row['away_team']
        hh = team_home.get(ht, [])
        aa = team_away.get(at, [])

        def ht_rate(hist, n, outcome):
            h = hist[-n:]
            return sum(1 for r in h if r == outcome) / max(len(h), 1)

        r2 = row.copy()
        # Home team's HT lead rate at home
        r2['h_ht_lead_rate']  = ht_rate(hh, 10, 'H')   # how often leads at HT when at home
        r2['h_ht_draw_rate']  = ht_rate(hh, 10, 'D')
        r2['h_ht_trail_rate'] = ht_rate(hh, 10, 'A')
        # Away team's HT trail rate away
        r2['a_ht_lead_rate']  = ht_rate(aa, 10, 'A')   # away team leads at HT when away
        r2['a_ht_draw_rate']  = ht_rate(aa, 10, 'D')
        r2['a_ht_trail_rate'] = ht_rate(aa, 10, 'H')

        ht_res = row.get('ht_result', '')
        r2['ht_result_actual'] = ht_res

        if ht_res and ht_res in ['H', 'D', 'A']:
            team_home.setdefault(ht, []).append(ht_res)
            team_away.setdefault(at, []).append(ht_res)

        rows_out.append(r2)
    return pd.DataFrame(rows_out)


def scorer_ht(row) -> tuple:
    """
    Returns (pick, confidence, description) or (None, 0, None).
    pick = 'H' (home leads at HT), 'D' (draw at HT), 'A' (away leads at HT)

    Approach:
    - Use Pinnacle FT prob as baseline (calibrated to HT rates)
    - Boost/penalise based on team's actual HT record
    - Gate: only pick when signal is very strong — we have no HT odds data
      so we need high confidence to compensate
    """
    ph  = row.get('pinnacle_prob_h', 0)
    pa  = row.get('pinnacle_prob_a', 0)
    h_lead  = row.get('h_ht_lead_rate', 0)
    a_lead  = row.get('a_ht_lead_rate', 0)
    h_draw  = row.get('h_ht_draw_rate', 0)
    a_draw  = row.get('a_ht_draw_rate', 0)

    # Historical HT calibration from data:
    # ph>70% → HT-H 55.6%; ph 55-70% → HT-H 48%; ph 40-55% → HT-H 36.5%
    # Adjust ph for HT: roughly ph * 0.78 for HT lead probability

    ht_h_prob = ph * 0.78            # calibrated HT home lead probability
    ht_a_prob = pa * 0.78            # calibrated HT away lead probability
    ht_d_prob = 1 - ht_h_prob - ht_a_prob

    candidates = []

    # HOME leads at HT
    # Gate: Pinnacle heavily favours home (ph>0.68) + home team historically leads at HT often
    if ph >= 0.68 and h_lead >= 0.40:
        score = ht_h_prob * 10
        if h_lead >= 0.50:  score *= 1.25   # home team leads HT >50% of home games
        if a_lead < 0.15:   score *= 1.15   # away team rarely leads at HT away
        if ph >= 0.75:      score *= 1.15   # very strong favourite
        candidates.append(('H', round(score, 1),
                           f"ph={ph:.0%}, home HT lead rate {h_lead:.0%} at home"))

    # AWAY leads at HT
    if pa >= 0.62 and a_lead >= 0.35:
        score = ht_a_prob * 10
        if a_lead >= 0.45:  score *= 1.25
        if h_lead < 0.20:   score *= 1.15
        if pa >= 0.70:      score *= 1.15
        candidates.append(('A', round(score, 1),
                           f"pa={pa:.0%}, away HT lead rate {a_lead:.0%} away"))

    # DRAW at HT — most common outcome (40%) but hard to predict.
    # Only pick when both teams very evenly matched AND both draw-prone at HT
    if abs(ph - pa) < 0.08 and h_draw >= 0.35 and a_draw >= 0.35:
        score = ht_d_prob * 10 * (h_draw + a_draw)
        candidates.append(('D', round(score, 1),
                           f"tight match ph={ph:.0%}/pa={pa:.0%}, HT draw rate: home {h_draw:.0%} away {a_draw:.0%}"))

    if not candidates:
        return None, 0, None

    best = max(candidates, key=lambda x: x[1])
    return best if best[1] >= 5 else (None, 0, None)


def run_ht_backtest() -> dict:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT league, date, home_team, away_team,
               ht_result, ht_home_goals, ht_away_goals,
               home_goals, away_goals, result,
               pinnacle_prob_h, pinnacle_prob_d, pinnacle_prob_a,
               pinnacle_h, pinnacle_d, pinnacle_a
        FROM matches_history
        WHERE ht_result IS NOT NULL AND ht_result != ''
          AND pinnacle_prob_h IS NOT NULL
        ORDER BY date
    """, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    league_dfs = []
    for league, ldf in df.groupby('league'):
        league_dfs.append(build_ht_features(ldf.sort_values('date').reset_index(drop=True)))
    df = pd.concat(league_dfs).sort_values('date').reset_index(drop=True)

    scored = df.apply(scorer_ht, axis=1)
    df['pick']    = scored.apply(lambda x: x[0])
    df['conf']    = scored.apply(lambda x: x[1])
    df['reason']  = scored.apply(lambda x: x[2])
    df['correct'] = df.apply(lambda r: r['pick'] == r['ht_result_actual'], axis=1)

    df_picks = df[df['pick'].notna()].copy()
    df_picks['week'] = df_picks['date'].dt.to_period('W')

    # Best 1 per week
    csv_rows, results = [], []
    for week, grp in df_picks.groupby('week'):
        best = grp.sort_values('conf', ascending=False).iloc[0]
        csv_rows.append({
            'week':       str(week),
            'match':      f"{best['home_team']} vs {best['away_team']}",
            'league':     best['league'],
            'pick':       best['pick'],
            'actual_ht':  best['ht_result_actual'],
            'actual_ft':  best['result'],
            'ht_correct': best['correct'],
            'ht_score':   f"{int(best.get('ht_home_goals') or 0)}-{int(best.get('ht_away_goals') or 0)}",
            'confidence': best['conf'],
            'pin_prob_h': round(best['pinnacle_prob_h'], 2),
            'pin_prob_a': round(best['pinnacle_prob_a'], 2),
            'h_ht_lead':  round(best['h_ht_lead_rate'], 2),
            'a_ht_lead':  round(best['a_ht_lead_rate'], 2),
            'reason':     best['reason'],
        })
        results.append({'correct': best['correct'], 'pick': best['pick']})

    out_df = pd.DataFrame(csv_rows).sort_values('week').reset_index(drop=True)
    out_path = REPORT_DIR / "ht_backtest.csv"
    out_df.to_csv(out_path, index=False)

    total = len(results)
    wins  = sum(1 for r in results if r['correct'])

    print(f"\nFirst Half Result Backtest")
    print(f"  Weeks: {total} | Correct: {wins} ({wins/total:.1%})")

    print(f"\n  By pick type:")
    for pick in ['H', 'D', 'A']:
        sub = out_df[out_df['pick'] == pick]
        if len(sub) == 0: continue
        acc = sub['ht_correct'].mean()
        print(f"    HT={pick}: {len(sub):>4} picks | {acc:.1%} accuracy")

    # Threshold analysis
    print(f"\n  Threshold analysis:")
    print(f"  {'Min conf':>8} | {'Weeks':>5} | {'Acc':>6}")
    for thresh in [0, 5, 6, 7, 8, 9, 10]:
        sub = out_df[out_df['confidence'] >= thresh]
        if len(sub) < 5: break
        print(f"  {thresh:>8} | {len(sub):>5} | {sub['ht_correct'].mean():>6.1%}")

    print(f"\n  Saved: {out_path}")
    return {'total': total, 'wins': wins, 'win_pct': wins/total if total else 0}


if __name__ == "__main__":
    run_ht_backtest()
