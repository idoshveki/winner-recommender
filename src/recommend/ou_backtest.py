"""
Over/Under 2.5 goals backtest.
Winner market: "מעל/מתחת 2.5 שערים" — Over / Under (no draw option on this line).

Strategy: find matches where our model strongly disagrees with the market,
or where a consistent pattern (both teams high-scoring, defensive records) gives edge.

Features used:
  - Pinnacle O/U 2.5 implied prob (primary signal)
  - Rolling home/away goals scored + conceded (last 5)
  - Over rate: how often does this match-up produce >2.5 goals
  - League baseline (Bundesliga 60% over, La Liga 46%)

Gate logic:
  - OVER pick:  pinnacle_over_prob > 0.60 AND both teams avg goals > 1.4/game
  - UNDER pick: pinnacle_under_prob > 0.55 AND both teams avg goals < 1.1/game AND both avg conceded < 1.0

Pick 1 best O/U per week (highest confidence), separate from 1X2 accumulator.
"""

import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

ROOT       = Path(__file__).resolve().parents[2]
DB_PATH    = ROOT / "data" / "db" / "winner.db"
REPORT_DIR = ROOT / "data" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ── Feature builder ───────────────────────────────────────────────────────────

def build_ou_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add rolling goals features per team. Process one league at a time."""
    team_home = {}   # home team's home games: list of (date, gf, ga, total)
    team_away = {}   # away team's away games

    rows_out = []
    for _, row in df.iterrows():
        ht, at = row['home_team'], row['away_team']

        hh = team_home.get(ht, [])   # home team at home
        aa = team_away.get(at, [])   # away team away

        def avg_gf(hist, n):
            h = hist[-n:]
            return sum(g for _, g, _, _ in h) / max(len(h), 1)
        def avg_ga(hist, n):
            h = hist[-n:]
            return sum(g for _, _, g, _ in h) / max(len(h), 1)
        def over_rate(hist, n, threshold=2):
            h = hist[-n:]
            return sum(1 for _, gf, ga, _ in h if gf + ga > threshold) / max(len(h), 1)

        r2 = row.copy()
        r2['h_home_gf5']   = avg_gf(hh, 5)
        r2['h_home_ga5']   = avg_ga(hh, 5)
        r2['a_away_gf5']   = avg_gf(aa, 5)
        r2['a_away_ga5']   = avg_ga(aa, 5)
        r2['h_home_over5'] = over_rate(hh, 5)   # how often home games go over 2
        r2['a_away_over5'] = over_rate(aa, 5)
        r2['combined_gf']  = r2['h_home_gf5'] + r2['a_away_gf5']   # expected goals proxy
        r2['combined_ga']  = r2['h_home_ga5'] + r2['a_away_ga5']

        # Record result
        total = int(row['home_goals'] or 0) + int(row['away_goals'] or 0)
        r2['total_goals'] = total
        r2['over25']      = total > 2

        hg, ag = int(row['home_goals'] or 0), int(row['away_goals'] or 0)
        team_home.setdefault(ht, []).append((row['date'], hg, ag, hg+ag))
        team_away.setdefault(at, []).append((row['date'], ag, hg, hg+ag))

        rows_out.append(r2)
    return pd.DataFrame(rows_out)


# ── Scorer ────────────────────────────────────────────────────────────────────

def scorer_ou(row) -> tuple:
    """
    Returns (pick, confidence, odds) or (None, 0, None).
    pick = 'OVER' or 'UNDER'
    """
    po = row.get('pinnacle_over_prob')
    pu = row.get('pinnacle_under_prob')
    if po is None or pu is None:
        return None, 0, None

    cgf = row.get('combined_gf', 0)    # sum of home's home gf + away's away gf
    cga = row.get('combined_ga', 0)
    h_over = row.get('h_home_over5', 0)
    a_over = row.get('a_away_over5', 0)
    combined_over_rate = (h_over + a_over) / 2

    # OVER: Pinnacle strongly favours over + both teams historically high-scoring
    if po >= 0.60:
        score = po * 10
        if cgf > 3.0:     score *= 1.30   # both teams score loads
        if cgf > 3.5:     score *= 1.20   # extra boost
        if combined_over_rate > 0.65: score *= 1.20
        if score >= 6.5:
            return ('OVER', round(score, 1), row['pinnacle_over25'])

    # UNDER: Pinnacle favours under + both teams low-scoring + tight defences
    if pu >= 0.55:
        score = pu * 10
        if cgf < 2.2:     score *= 1.30   # both teams score little
        if cga < 2.0:     score *= 1.20   # both teams defend well
        if combined_over_rate < 0.35: score *= 1.20
        if score >= 6.0:
            return ('UNDER', round(score, 1), row['pinnacle_under25'])

    return None, 0, None


# ── Backtest ─────────────────────────────────────────────────────────────────

def run_ou_backtest(min_conf: float = 0) -> dict:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT league, date, home_team, away_team,
               home_goals, away_goals,
               pinnacle_over25, pinnacle_under25,
               avg_over25, avg_under25
        FROM matches_history
        WHERE pinnacle_over25 IS NOT NULL
          AND home_goals IS NOT NULL
        ORDER BY date
    """, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # Implied probs (vig-removed)
    df['pinnacle_over_prob']  = (1 / df['pinnacle_over25'])
    df['pinnacle_under_prob'] = (1 / df['pinnacle_under25'])
    vig = df['pinnacle_over_prob'] + df['pinnacle_under_prob']
    df['pinnacle_over_prob']  /= vig
    df['pinnacle_under_prob'] /= vig

    # Build features per league
    league_dfs = []
    for league, ldf in df.groupby('league'):
        ldf = ldf.sort_values('date').reset_index(drop=True)
        league_dfs.append(build_ou_features(ldf))
    df = pd.concat(league_dfs).sort_values('date').reset_index(drop=True)

    # Score
    df['pick'], df['conf'], df['bet_odds'] = zip(*df.apply(scorer_ou, axis=1))
    df_picks = df[df['pick'].notna()].copy()
    df_picks['week'] = df_picks['date'].dt.to_period('W')

    # Best 1 pick per week
    csv_rows = []
    results  = []
    for week, grp in df_picks.groupby('week'):
        grp = grp.sort_values('conf', ascending=False)
        best = grp.iloc[0]
        if min_conf > 0 and best['conf'] < min_conf:
            continue

        correct = (best['pick'] == 'OVER' and best['over25']) or \
                  (best['pick'] == 'UNDER' and not best['over25'])

        csv_rows.append({
            'week':         str(week),
            'match':        f"{best['home_team']} vs {best['away_team']}",
            'league':       best['league'],
            'pick':         best['pick'],
            'odds':         round(best['bet_odds'], 2),
            'actual_goals': int(best['total_goals']),
            'over25':       bool(best['over25']),
            'correct':      correct,
            'confidence':   best['conf'],
            'pin_over_prob':round(best['pinnacle_over_prob'], 3),
            'pin_under_prob':round(best['pinnacle_under_prob'], 3),
            'combined_gf':  round(best['combined_gf'], 2),
            'combined_ga':  round(best['combined_ga'], 2),
            'h_over_rate':  round(best['h_home_over5'], 2),
            'a_over_rate':  round(best['a_away_over5'], 2),
        })
        results.append({'correct': correct, 'odds': best['bet_odds']})

    out_df = pd.DataFrame(csv_rows).sort_values('week').reset_index(drop=True)
    out_path = REPORT_DIR / "ou_backtest.csv"
    out_df.to_csv(out_path, index=False)

    total  = len(results)
    wins   = sum(1 for r in results if r['correct'])
    avg_o  = np.mean([r['odds'] for r in results]) if results else 0
    ev     = (wins / total) * avg_o if total else 0

    # Threshold analysis
    print(f"\nO/U 2.5 Backtest (min_conf={min_conf})")
    print(f"  Picks: {total} weeks | Correct: {wins} ({wins/total:.1%}) | Avg odds: {avg_o:.2f} | EV: {ev:.3f}")

    over_df  = out_df[out_df['pick']=='OVER']
    under_df = out_df[out_df['pick']=='UNDER']
    if len(over_df):
        oa = over_df['correct'].mean()
        print(f"  OVER:  {len(over_df)} picks | {oa:.1%} acc | {over_df['odds'].mean():.2f} odds | EV {oa*over_df['odds'].mean():.3f}")
    if len(under_df):
        ua = under_df['correct'].mean()
        print(f"  UNDER: {len(under_df)} picks | {ua:.1%} acc | {under_df['odds'].mean():.2f} odds | EV {ua*under_df['odds'].mean():.3f}")

    print(f"  Saved: {out_path}")
    return {'total': total, 'wins': wins, 'win_pct': wins/total if total else 0,
            'avg_odds': avg_o, 'ev': ev, 'csv': str(out_path)}


if __name__ == "__main__":
    # Run with no threshold first to see full picture
    run_ou_backtest(min_conf=0)

    # Then test thresholds
    print("\n--- Threshold analysis ---")
    print(f"{'Min conf':>8} | {'Weeks':>5} | {'Acc':>6} | {'EV':>5}")
    print("-" * 35)

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT league, date, home_team, away_team, home_goals, away_goals,
               pinnacle_over25, pinnacle_under25
        FROM matches_history
        WHERE pinnacle_over25 IS NOT NULL AND home_goals IS NOT NULL
        ORDER BY date
    """, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df['pinnacle_over_prob']  = 1 / df['pinnacle_over25']
    df['pinnacle_under_prob'] = 1 / df['pinnacle_under25']
    vig = df['pinnacle_over_prob'] + df['pinnacle_under_prob']
    df['pinnacle_over_prob']  /= vig
    df['pinnacle_under_prob'] /= vig

    league_dfs = []
    for league, ldf in df.groupby('league'):
        league_dfs.append(build_ou_features(ldf.sort_values('date').reset_index(drop=True)))
    df = pd.concat(league_dfs).sort_values('date').reset_index(drop=True)
    df['pick'], df['conf'], df['bet_odds'] = zip(*df.apply(scorer_ou, axis=1))
    df_picks = df[df['pick'].notna()].copy()
    df_picks['week'] = df_picks['date'].dt.to_period('W')
    df_picks['correct'] = df_picks.apply(
        lambda r: (r['pick']=='OVER' and r['over25']) or (r['pick']=='UNDER' and not r['over25']), axis=1)

    best_per_week = df_picks.groupby('week').apply(lambda g: g.sort_values('conf', ascending=False).iloc[0]).reset_index(drop=True)

    for thresh in [0, 7, 8, 9, 10, 11, 12]:
        sub = best_per_week[best_per_week['conf'] >= thresh]
        if len(sub) < 5: break
        acc = sub['correct'].mean()
        ao  = sub['bet_odds'].mean()
        print(f"{thresh:>8} | {len(sub):>5} | {acc:>6.1%} | {acc*ao:>5.3f}")
