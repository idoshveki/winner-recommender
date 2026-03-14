"""
Corners backtest — two Winner markets:
  1. Total corners range: ≤8 / 9-11 / 12+
  2. First corner team: Home / Away (skip draw at 9.6%)

Strategy: teams have very consistent corner styles. High-pressing, wide-play teams
always generate corners. Defensive, counter-attacking teams avoid them.
Rolling corner averages are highly predictive of match total.

Pick 1 best corners bet per week.
"""

import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

ROOT       = Path(__file__).resolve().parents[2]
DB_PATH    = ROOT / "data" / "db" / "winner.db"
REPORT_DIR = ROOT / "data" / "reports"


def build_corner_features(df: pd.DataFrame) -> pd.DataFrame:
    team_home = {}   # home team's home games
    team_away = {}   # away team's away games

    rows_out = []
    for _, row in df.iterrows():
        ht, at = row['home_team'], row['away_team']
        hh = team_home.get(ht, [])
        aa = team_away.get(at, [])

        def avg_corners(hist, n, idx):   # idx 1=for, 2=against (0=date string)
            h = hist[-n:]
            return sum(x[idx] for x in h) / max(len(h), 1)

        r2 = row.copy()
        # Home team: corners for (home_corners) and against (away_corners) at home
        r2['h_home_cf5'] = avg_corners(hh, 5, 1)   # home corners when at home
        r2['h_home_ca5'] = avg_corners(hh, 5, 2)   # away corners when home plays at home
        # Away team: corners for and against when away
        r2['a_away_cf5'] = avg_corners(aa, 5, 1)   # away team's corners when away
        r2['a_away_ca5'] = avg_corners(aa, 5, 2)   # home corners when this away team visits

        # Expected total corners: home team generates + away team generates
        r2['exp_total_corners'] = r2['h_home_cf5'] + r2['a_away_cf5']
        # First corner signal: home generates more than away
        r2['exp_first_home'] = r2['h_home_cf5'] / max(r2['h_home_cf5'] + r2['a_away_cf5'], 0.1)

        hc = int(row.get('home_corners') or 0)
        ac = int(row.get('away_corners') or 0)
        total = hc + ac
        r2['total_corners'] = total
        r2['corner_bucket'] = '≤8' if total <= 8 else ('9-11' if total <= 11 else '12+')
        r2['first_corner']  = 'H' if hc > ac else ('A' if ac > hc else 'D')

        team_home.setdefault(ht, []).append((str(row['date'])[:10], hc, ac))
        team_away.setdefault(at, []).append((str(row['date'])[:10], ac, hc))

        rows_out.append(r2)
    return pd.DataFrame(rows_out)


def scorer_corners(row) -> tuple:
    """
    Returns (pick, confidence, market, description) or (None, 0, None, None).
    Markets:
      'total_low'  → bet ≤8 corners
      'total_high' → bet 12+ corners
      'first_home' → bet home team gets first corner
      'first_away' → bet away team gets first corner
    """
    exp = row.get('exp_total_corners', 0)
    exp_h = row.get('exp_first_home', 0.5)
    n_home = len([x for x in [row.get('h_home_cf5')] if x is not None])

    candidates = []

    # ── Total corners: LOW (≤8) ───────────────────────────────────────────────
    # Both teams are low-corner teams at their respective venues
    if exp < 7.5:
        score = (7.5 - exp) * 3    # bigger gap = more confident
        candidates.append(('≤8 corners', score, 'total_low',
                           f"exp {exp:.1f} corners (home gen {row.get('h_home_cf5',0):.1f}, away gen {row.get('a_away_cf5',0):.1f})"))

    # ── Total corners: HIGH (12+) ─────────────────────────────────────────────
    if exp > 12.5:
        score = (exp - 12.5) * 3
        candidates.append(('12+ corners', score, 'total_high',
                           f"exp {exp:.1f} corners (home gen {row.get('h_home_cf5',0):.1f}, away gen {row.get('a_away_cf5',0):.1f})"))

    # ── First corner: HOME ────────────────────────────────────────────────────
    # Home generates significantly more corners than away
    if exp_h > 0.65:
        score = (exp_h - 0.50) * 20
        candidates.append(('First corner: Home', score, 'first_home',
                           f"home generates {exp_h:.0%} of expected corners"))

    # ── First corner: AWAY ────────────────────────────────────────────────────
    if exp_h < 0.35:
        score = (0.50 - exp_h) * 20
        candidates.append(('First corner: Away', score, 'first_away',
                           f"away generates {1-exp_h:.0%} of expected corners"))

    if not candidates:
        return None, 0, None, None

    best = max(candidates, key=lambda x: x[1])
    return best if best[1] >= 4 else (None, 0, None, None)


def run_corners_backtest() -> dict:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT league, date, home_team, away_team,
               home_corners, away_corners, home_goals, away_goals
        FROM matches_history
        WHERE home_corners IS NOT NULL
        ORDER BY date
    """, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    league_dfs = []
    for league, ldf in df.groupby('league'):
        league_dfs.append(build_corner_features(ldf.sort_values('date').reset_index(drop=True)))
    df = pd.concat(league_dfs).sort_values('date').reset_index(drop=True)

    scored = df.apply(scorer_corners, axis=1)
    df['c_label']  = scored.apply(lambda x: x[0])
    df['c_score']  = scored.apply(lambda x: x[1])
    df['c_market'] = scored.apply(lambda x: x[2])
    df['c_reason'] = scored.apply(lambda x: x[3])

    df_picks = df[df['c_label'].notna()].copy()
    df_picks['week'] = df_picks['date'].dt.to_period('W')

    # Actual result check per market
    def is_correct(row):
        m = row['c_market']
        if m == 'total_low':   return row['corner_bucket'] == '≤8'
        if m == 'total_high':  return row['corner_bucket'] == '12+'
        if m == 'first_home':  return row['first_corner'] == 'H'
        if m == 'first_away':  return row['first_corner'] == 'A'
        return False

    df_picks['correct'] = df_picks.apply(is_correct, axis=1)

    # Best 1 per week
    csv_rows, results = [], []
    for week, grp in df_picks.groupby('week'):
        best = grp.sort_values('c_score', ascending=False).iloc[0]
        csv_rows.append({
            'week':          str(week),
            'match':         f"{best['home_team']} vs {best['away_team']}",
            'league':        best['league'],
            'pick':          best['c_label'],
            'market':        best['c_market'],
            'correct':       best['correct'],
            'confidence':    round(best['c_score'], 1),
            'actual_total':  int(best['total_corners']),
            'actual_bucket': best['corner_bucket'],
            'first_corner':  best['first_corner'],
            'exp_corners':   round(best['exp_total_corners'], 1),
            'reason':        best['c_reason'],
        })
        results.append({'correct': best['correct'], 'market': best['c_market']})

    out_df = pd.DataFrame(csv_rows).sort_values('week').reset_index(drop=True)
    out_path = REPORT_DIR / "corners_backtest.csv"
    out_df.to_csv(out_path, index=False)

    total = len(results)
    wins  = sum(1 for r in results if r['correct'])

    print(f"\nCorners Backtest")
    print(f"  Weeks: {total} | Correct: {wins} ({wins/total:.1%})")
    print(f"\n  By market:")
    for mkt in ['total_low', 'total_high', 'first_home', 'first_away']:
        sub = out_df[out_df['market'] == mkt]
        if len(sub) == 0: continue
        acc = sub['correct'].mean()
        print(f"    {mkt:<15}: {len(sub):>4} picks | {acc:.1%} accuracy")

    # Threshold analysis
    print(f"\n  Threshold analysis (total accuracy):")
    print(f"  {'Min conf':>8} | {'Weeks':>5} | {'Acc':>6}")
    for thresh in [0, 4, 5, 6, 7, 8, 10]:
        sub = out_df[out_df['confidence'] >= thresh]
        if len(sub) < 5: break
        print(f"  {thresh:>8} | {len(sub):>5} | {sub['correct'].mean():>6.1%}")

    print(f"\n  Saved: {out_path}")
    return {'total': total, 'wins': wins, 'win_pct': wins/total if total else 0}


if __name__ == "__main__":
    run_corners_backtest()
