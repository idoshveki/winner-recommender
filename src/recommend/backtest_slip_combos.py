"""
Phase 1 backtest: simulate all slip combinations across historical weeks.

Tests:
  - HA only (baseline)
  - HA + YC
  - HA + BTTS
  - HA + YC + YC  (2 YC legs)
  - HA + YC + BTTS

Assumed odds:
  - H/A: actual Pinnacle odds from DB (B365 as fallback)
  - YC Over 3.5: 1.50 (1.60 Bundesliga)
  - BTTS + O2.5: 2.10

Usage:
  source .venv/bin/activate
  python src/recommend/backtest_slip_combos.py
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import timedelta
from itertools import product

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "db" / "winner.db"
OUT_DIR = ROOT / "data" / "reports"

UNRELIABLE_HOME = {'Tottenham', 'Man United', 'Chelsea', 'Brighton', 'West Ham', 'Bournemouth'}
YC_ODDS_DEFAULT = 1.50
YC_ODDS_BUNDESLIGA = 1.60
BTTS_ODDS = 2.10


# ── load data ──────────────────────────────────────────────────────────────────

def load_data():
    conn = sqlite3.connect(DB_PATH)
    hist = pd.read_sql("""
        SELECT league, date, home_team, away_team,
               home_goals, away_goals, result,
               home_yellow, away_yellow,
               pinnacle_h, pinnacle_d, pinnacle_a,
               pinnacle_prob_h, pinnacle_prob_d, pinnacle_prob_a,
               b365_h, b365_d, b365_a
        FROM matches_history
        WHERE result IS NOT NULL
        ORDER BY date
    """, conn)
    conn.close()
    hist['date'] = pd.to_datetime(hist['date'])
    return hist.sort_values('date').reset_index(drop=True)


# ── feature helpers ────────────────────────────────────────────────────────────

def get_ha_odds(row):
    """Return (ph, pd_, pa, oh, od, oa) Pinnacle implied probs + raw odds. Fallback to B365."""
    for h_col, d_col, a_col in [('pinnacle_h','pinnacle_d','pinnacle_a'),
                                  ('b365_h','b365_d','b365_a')]:
        oh, od, oa = row.get(h_col), row.get(d_col), row.get(a_col)
        if pd.notna(oh) and pd.notna(od) and pd.notna(oa) and oh > 0 and od > 0 and oa > 0:
            inv = 1/oh + 1/od + 1/oa
            return (1/oh)/inv, (1/od)/inv, (1/oa)/inv, oh, od, oa
    return None, None, None, None, None, None


def venue_pts(games, result_col, win_char, n=5):
    results = games[result_col].tail(n * 2).tolist()[-n:]
    return sum(3 if r == win_char else (1 if r == 'D' else 0) for r in results)


def get_form(hist_before, team, venue, n=5):
    if venue == 'home':
        games = hist_before[hist_before['home_team'] == team]
        win_char = 'H'
        gf_col, ga_col = 'home_goals', 'away_goals'
        yc_col = 'home_yellow'
    else:
        games = hist_before[hist_before['away_team'] == team]
        win_char = 'A'
        gf_col, ga_col = 'away_goals', 'home_goals'
        yc_col = 'away_yellow'

    if len(games) < 3:
        return None

    last = games.tail(n * 2)
    results = last['result'].tail(n).tolist()
    pts = sum(3 if r == win_char else (1 if r == 'D' else 0) for r in results)

    recent3 = last['result'].tail(3).tolist()
    prior3  = last['result'].tail(6).head(3).tolist()
    trend = (sum(3 if r == win_char else (1 if r == 'D' else 0) for r in recent3) -
             sum(3 if r == win_char else (1 if r == 'D' else 0) for r in prior3))

    dr10 = (last['result'].tail(10) == 'D').mean()
    gf5  = last[gf_col].tail(n).mean()
    ga5  = last[ga_col].tail(n).mean()

    # streak
    all_res = last['result'].tail(10).tolist()
    streak = 0
    for r in reversed(all_res):
        if r == win_char: streak += 1
        else: break
    lstreak = 0
    lose_char = 'A' if venue == 'home' else 'H'
    for r in reversed(all_res):
        if r == lose_char: lstreak += 1
        else: break

    # yc rolling avg (last 5 venue games)
    yc_hist = games[yc_col].dropna().tail(5).tolist()

    return {
        'pts': pts, 'trend': trend, 'dr10': dr10,
        'gf5': round(gf5, 1) if not pd.isna(gf5) else 0,
        'ga5': round(ga5, 1) if not pd.isna(ga5) else 0,
        'streak': streak, 'lstreak': lstreak,
        'yc_hist': yc_hist,
    }


def ha_score(hf, af, ph, pa, ht, at, venue):
    """Confidence score for H/A pick (same logic as send_weekly.py)."""
    if venue == 'H':
        if ph < 0.63: return None
        if hf['pts'] - af['pts'] < 5: return None
        pts_gap = hf['pts'] - af['pts']
        venue_gap = hf['pts'] - af['pts']  # simplified; use pts as proxy
        if hf['trend'] < 0: return None
        if ht in UNRELIABLE_HOME and ph < 0.72: return None
        conf = ph * 100
        if hf['streak'] >= 3: conf *= 1.25
        if af['lstreak'] >= 2: conf *= 1.15
        if hf['gf5'] > af['ga5'] + 0.5: conf *= 1.20
        if hf['pts'] >= 12 and af['pts'] <= 3: conf *= 1.50
        return round(conf, 1)
    else:  # Away
        if pa < 0.58: return None
        if af['pts'] - hf['pts'] < 5: return None
        if af['trend'] < 0: return None
        conf = pa * 100
        if af['streak'] >= 3: conf *= 1.25
        if hf['lstreak'] >= 2: conf *= 1.15
        if af['gf5'] > hf['ga5'] + 0.5: conf *= 1.20
        if af['pts'] >= 12 and hf['pts'] <= 3: conf *= 1.50
        return round(conf, 1)


# ── per-week simulation ────────────────────────────────────────────────────────

def simulate_week(week_games, hist_before, yc_thresh, btts_home_thresh, btts_away_thresh):
    """
    For a given week's matches and history up to that point,
    return candidate picks for each market.
    """
    ha_picks, yc_picks, btts_picks = [], [], []

    for _, row in week_games.iterrows():
        ht, at = row['home_team'], row['away_team']
        league = row['league']

        ph, pd_, pa, oh, od, oa = get_ha_odds(row)
        if ph is None:
            continue

        hf = get_form(hist_before, ht, 'home')
        af = get_form(hist_before, at, 'away')
        if hf is None or af is None:
            continue

        # ── H/A ───────────────────────────────────────────────────────────────
        for venue, pick_prob, odds_val in [('H', ph, oh), ('A', pa, oa)]:
            conf = ha_score(hf, af, ph, pa, ht, at, venue)
            if conf and conf >= 13:
                ha_picks.append({
                    'match': f"{ht} vs {at}", 'league': league, 'date': row['date'],
                    'pick': venue, 'odds': odds_val, 'conf': conf,
                    'result': row['result'],  # actual outcome
                    'hit': (row['result'] == venue),
                })
                break  # only one H/A pick per match

        # ── YC ────────────────────────────────────────────────────────────────
        h_yc = hf['yc_hist']
        a_yc = af['yc_hist']
        if len(h_yc) >= 3 and len(a_yc) >= 3:
            yc_pred = sum(h_yc) / len(h_yc) + sum(a_yc) / len(a_yc)
            if yc_pred >= yc_thresh:
                yc_odds = YC_ODDS_BUNDESLIGA if league == 'Bundesliga' else YC_ODDS_DEFAULT
                actual_yc = row.get('home_yellow', 0) + row.get('away_yellow', 0)
                yc_picks.append({
                    'match': f"{ht} vs {at}", 'league': league, 'date': row['date'],
                    'yc_pred': round(yc_pred, 2), 'odds': yc_odds,
                    'actual_yc': actual_yc,
                    'hit': (pd.notna(actual_yc) and actual_yc > 3.5),
                })

        # ── BTTS + O2.5 ───────────────────────────────────────────────────────
        if hf['gf5'] >= btts_home_thresh and af['gf5'] >= btts_away_thresh:
            total_goals = row['home_goals'] + row['away_goals']
            btts_hit = (row['home_goals'] > 0 and row['away_goals'] > 0 and total_goals > 2.5)
            btts_picks.append({
                'match': f"{ht} vs {at}", 'league': league, 'date': row['date'],
                'home_gf5': hf['gf5'], 'away_gf5': af['gf5'],
                'odds': BTTS_ODDS,
                'hit': btts_hit,
            })

    # sort by confidence / yc_pred
    ha_picks.sort(key=lambda x: -x['conf'])
    yc_picks.sort(key=lambda x: -x['yc_pred'])

    return ha_picks, yc_picks, btts_picks


# ── slip evaluator ─────────────────────────────────────────────────────────────

def eval_slip(legs):
    """Given list of leg dicts with 'odds' and 'hit', return (combined_odds, won)."""
    combined = 1.0
    for leg in legs:
        combined *= leg['odds']
    won = all(leg['hit'] for leg in legs)
    return round(combined, 3), won


# ── main backtest ──────────────────────────────────────────────────────────────

def run_backtest():
    print("Loading data...")
    hist = load_data()

    # parameter grid
    yc_thresholds    = [3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
    btts_home_vals   = [1.5, 1.8, 2.0]
    btts_away_vals   = [1.3, 1.5, 1.8]

    # collect all results
    all_results = []

    # group matches into ISO weeks
    hist['week'] = hist['date'].dt.to_period('W')
    weeks = sorted(hist['week'].unique())

    print(f"Simulating {len(weeks)} weeks across parameter grid...")

    for yc_thresh, btts_h, btts_a in product(yc_thresholds, btts_home_vals, btts_away_vals):
        slips = {
            'HA':         [],
            'HA+YC':      [],
            'HA+BTTS':    [],
            'HA+YC+YC':   [],
            'HA+YC+BTTS': [],
        }

        for week in weeks:
            week_games  = hist[hist['week'] == week]
            hist_before = hist[hist['date'] < week_games['date'].min()]

            if len(hist_before) < 50:  # need enough history
                continue

            ha, yc, btts = simulate_week(week_games, hist_before, yc_thresh, btts_h, btts_a)

            if not ha:
                continue
            best_ha = ha[0]

            # ── HA only ──────────────────────────────────────────────────────
            odds, won = eval_slip([best_ha])
            slips['HA'].append({'odds': odds, 'won': won})

            # ── HA + YC ──────────────────────────────────────────────────────
            # best YC from a different match than HA leg 1
            yc_other = [p for p in yc if p['match'] != best_ha['match']]
            if yc_other:
                odds, won = eval_slip([best_ha, yc_other[0]])
                slips['HA+YC'].append({'odds': odds, 'won': won})

            # ── HA + BTTS ─────────────────────────────────────────────────────
            btts_other = [p for p in btts if p['match'] != best_ha['match']]
            if btts_other:
                odds, won = eval_slip([best_ha, btts_other[0]])
                slips['HA+BTTS'].append({'odds': odds, 'won': won})

            # ── HA + YC + YC ──────────────────────────────────────────────────
            if len(yc_other) >= 2:
                odds, won = eval_slip([best_ha, yc_other[0], yc_other[1]])
                slips['HA+YC+YC'].append({'odds': odds, 'won': won})

            # ── HA + YC + BTTS ────────────────────────────────────────────────
            if yc_other and btts_other:
                # use a BTTS pick different from the YC pick
                btts_not_yc = [p for p in btts_other if p['match'] != yc_other[0]['match']]
                if btts_not_yc:
                    odds, won = eval_slip([best_ha, yc_other[0], btts_not_yc[0]])
                    slips['HA+YC+BTTS'].append({'odds': odds, 'won': won})

        for combo, records in slips.items():
            if not records:
                continue
            df = pd.DataFrame(records)
            weeks_qual = len(df)
            win_rate   = df['won'].mean()
            avg_odds   = df['odds'].mean()
            ev         = win_rate * avg_odds
            all_results.append({
                'combo':        combo,
                'yc_thresh':    yc_thresh,
                'btts_home':    btts_h,
                'btts_away':    btts_a,
                'weeks':        weeks_qual,
                'win_rate':     round(win_rate, 3),
                'avg_odds':     round(avg_odds, 3),
                'ev':           round(ev, 3),
            })

    results = pd.DataFrame(all_results)
    results = results.sort_values(['combo', 'ev'], ascending=[True, False])

    out_path = OUT_DIR / "slip_combo_backtest.csv"
    results.to_csv(out_path, index=False)
    print(f"\nSaved full results to {out_path}")

    # ── summary: best config per combo ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("BEST CONFIGURATION PER SLIP TYPE (by EV, min 20 weeks)")
    print("=" * 70)
    for combo in ['HA', 'HA+YC', 'HA+BTTS', 'HA+YC+YC', 'HA+YC+BTTS']:
        sub = results[(results['combo'] == combo) & (results['weeks'] >= 20)]
        if sub.empty:
            print(f"\n{combo}: no config with ≥20 weeks")
            continue
        best = sub.iloc[0]
        print(f"\n{combo}:")
        print(f"  yc_thresh={best['yc_thresh']}  btts_home={best['btts_home']}  btts_away={best['btts_away']}")
        print(f"  weeks={best['weeks']}  win_rate={best['win_rate']:.1%}  avg_odds={best['avg_odds']:.2f}x  EV={best['ev']:.3f}")

    # ── YC standalone calibration ──────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("YC STANDALONE CALIBRATION (hit rate by yc_pred threshold)")
    print("=" * 70)
    yc_cal_rows = []
    for yc_thresh in yc_thresholds:
        records = []
        for week in weeks:
            week_games  = hist[hist['week'] == week]
            hist_before = hist[hist['date'] < week_games['date'].min()]
            if len(hist_before) < 50:
                continue
            _, yc, _ = simulate_week(week_games, hist_before, yc_thresh, 1.5, 1.3)
            records.extend(yc)
        if records:
            df = pd.DataFrame(records)
            hit = df['hit'].mean()
            yc_cal_rows.append({
                'yc_thresh': yc_thresh,
                'games': len(df),
                'hit_rate': round(hit, 3),
                'ev_150': round(hit * 1.50 - 1, 3),
                'ev_160': round(hit * 1.60 - 1, 3),
            })
    yc_cal = pd.DataFrame(yc_cal_rows)
    print(yc_cal.to_string(index=False))

    # ── BTTS standalone calibration ────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("BTTS+O2.5 STANDALONE CALIBRATION (hit rate by threshold)")
    print("=" * 70)
    btts_cal_rows = []
    for btts_h, btts_a in product(btts_home_vals, btts_away_vals):
        records = []
        for week in weeks:
            week_games  = hist[hist['week'] == week]
            hist_before = hist[hist['date'] < week_games['date'].min()]
            if len(hist_before) < 50:
                continue
            _, _, btts = simulate_week(week_games, hist_before, 3.5, btts_h, btts_a)
            records.extend(btts)
        if records:
            df = pd.DataFrame(records)
            hit = df['hit'].mean()
            btts_cal_rows.append({
                'btts_home': btts_h, 'btts_away': btts_a,
                'games': len(df),
                'hit_rate': round(hit, 3),
                'ev_210': round(hit * 2.10 - 1, 3),
            })
    btts_cal = pd.DataFrame(btts_cal_rows).sort_values('ev_210', ascending=False)
    print(btts_cal.to_string(index=False))


if __name__ == "__main__":
    run_backtest()
