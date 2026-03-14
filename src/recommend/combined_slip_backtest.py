"""
Combined multi-market slip backtest.

New structure (replacing 4 separate bets):
  Slip 1 (COMBINED): best H/A pick + best HT pick + best 1st corner pick
  Slip 2 (DRAW):     standalone draw single (unchanged)

Hypothesis: mixing 3 high-accuracy markets (~76% × ~67% × ~95%) into one
parlay gives ~48% win rate at meaningfully higher combined odds vs each bet alone.

We only have Pinnacle 1X2 odds for H/A. HT and corner odds are estimated:
  - HT odds ≈ Pinnacle FT odds × 0.78 calibration factor
  - First corner odds: Winner typically offers ~1.75–2.0 for H/A; we use 1.80 as proxy
"""

import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

ROOT       = Path(__file__).resolve().parents[2]
DB_PATH    = ROOT / "data" / "db" / "winner.db"
REPORT_DIR = ROOT / "data" / "reports"

# ── Feature builders (copied from individual backtests) ───────────────────────

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build all form features: H/A, HT, corners."""
    team_home = {}
    team_away = {}
    rows_out = []

    for _, row in df.iterrows():
        ht, at = row['home_team'], row['away_team']
        hh = team_home.get(ht, [])
        aa = team_away.get(at, [])

        def pts(res, side):
            if res == side: return 3
            if res == 'D':  return 1
            return 0

        def last_n_pts(hist, n, side):
            return sum(pts(r['result'], side) for r in hist[-n:])

        def draw_rate(hist, n):
            h = hist[-n:]
            return sum(1 for r in h if r['result'] == 'D') / max(len(h), 1)

        def win_streak(hist, side):
            s = 0
            for r in reversed(hist):
                if r['result'] == side: s += 1
                else: break
            return s

        def lose_streak(hist, side):
            loss = 'H' if side == 'A' else 'A'
            s = 0
            for r in reversed(hist):
                if r['result'] == loss: s += 1
                else: break
            return s

        def avg_goals(hist, n, key):
            h = hist[-n:]
            return sum(r[key] for r in h) / max(len(h), 1)

        def ht_rate(hist, n, outcome):
            h = hist[-n:]
            return sum(1 for r in h if r.get('ht_result') == outcome) / max(len(h), 1)

        def avg_corners(hist, n, key):
            h = hist[-n:]
            return sum(r[key] for r in h) / max(len(h), 1)

        home_pts5        = last_n_pts(hh, 5, 'H')
        away_pts5        = last_n_pts(aa, 5, 'A')
        home_venue_pts5  = last_n_pts(hh, 5, 'H')  # home team's home pts
        away_venue_pts5  = last_n_pts(aa, 5, 'A')  # away team's away pts
        home_all_pts5    = last_n_pts([r for r in hh] + [r for r in team_home.get(ht, [])], 5, 'H')
        away_all_pts5    = last_n_pts([r for r in aa] + [r for r in team_away.get(at, [])], 5, 'A')

        r2 = row.copy()
        r2['home_pts5']       = home_pts5
        r2['away_pts5']       = away_pts5
        r2['venue_gap']       = home_venue_pts5 - away_venue_pts5
        r2['pts5_diff']       = home_pts5 - away_pts5
        r2['home_trend']      = last_n_pts(hh, 3, 'H') - last_n_pts(hh[:-3] if len(hh)>3 else hh, 3, 'H')
        r2['away_trend']      = last_n_pts(aa, 3, 'A') - last_n_pts(aa[:-3] if len(aa)>3 else aa, 3, 'A')
        r2['home_winstreak']  = win_streak(hh, 'H')
        r2['away_losestreak'] = lose_streak(aa, 'A')
        r2['home_dr10']       = draw_rate(hh, 10)
        r2['away_dr10']       = draw_rate(aa, 10)
        r2['home_gf5']        = avg_goals(hh, 5, 'hg')
        r2['home_ga5']        = avg_goals(hh, 5, 'ag')
        r2['away_gf5']        = avg_goals(aa, 5, 'ag')
        r2['away_ga5']        = avg_goals(aa, 5, 'hg')

        # HT features
        r2['h_ht_lead_rate']  = ht_rate(hh, 10, 'H')
        r2['a_ht_lead_rate']  = ht_rate(aa, 10, 'A')

        # Corner features
        r2['h_home_cf5'] = avg_corners(hh, 5, 'hc')
        r2['a_away_cf5'] = avg_corners(aa, 5, 'ac')
        exp_h_c = r2['h_home_cf5']
        exp_a_c = r2['a_away_cf5']
        total_exp = exp_h_c + exp_a_c
        r2['exp_first_home'] = exp_h_c / max(total_exp, 0.1)

        # Actual result
        hg, ag = int(row.get('home_goals') or 0), int(row.get('away_goals') or 0)
        hc_act = int(row.get('home_corners') or 0) if pd.notna(row.get('home_corners')) else 0
        ac_act = int(row.get('away_corners') or 0) if pd.notna(row.get('away_corners')) else 0
        r2['actual_first_corner'] = 'H' if hc_act > ac_act else ('A' if ac_act > hc_act else 'D')
        r2['actual_result']       = row.get('result', '')
        r2['actual_ht_result']    = row.get('ht_result', '')

        # Update histories
        entry = {
            'result': row.get('result', ''),
            'ht_result': row.get('ht_result', ''),
            'hg': hg, 'ag': ag,
            'hc': hc_act, 'ac': ac_act,
        }
        team_home.setdefault(ht, []).append(entry)
        team_away.setdefault(at, []).append(entry)

        rows_out.append(r2)

    return pd.DataFrame(rows_out)


# ── Scorers ───────────────────────────────────────────────────────────────────

UNRELIABLE_HOME = {'Tottenham', 'Man United', 'Chelsea', 'Brighton',
                   'West Ham', 'Bournemouth'}

def score_ha(row):
    """Returns (pick, conf, odds, reason) or None."""
    ph  = row.get('prob_h', 0) or 0
    pa  = row.get('prob_a', 0) or 0
    vg  = row.get('venue_gap', 0) or 0
    pd5 = row.get('pts5_diff', 0) or 0
    ht_name = row.get('home_team', '')

    # HOME
    if ph >= 0.63 and vg >= 5 and pd5 >= 5 and row.get('home_trend', 0) >= 0:
        if ht_name in UNRELIABLE_HOME and ph < 0.72:
            pass
        else:
            conf = ph * 10
            if row.get('home_winstreak', 0) >= 3:  conf *= 1.25
            elif row.get('home_winstreak', 0) >= 2: conf *= 1.10
            if row.get('away_losestreak', 0) >= 2:  conf *= 1.15
            if row.get('home_gf5', 0) > 1.8 and row.get('home_ga5', 0) < 1.5: conf *= 1.20
            if row.get('home_pts5', 0) >= 12 and row.get('away_pts5', 0) <= 3:  conf *= 1.50
            pin_odds = row.get('odds_h')
            if pin_odds and pin_odds > 0:
                return ('H', round(conf, 1), float(pin_odds),
                        f"ph={ph:.0%} vg={vg} pd5={pd5}")

    # AWAY
    if pa >= 0.58 and vg <= -5 and pd5 <= -5 and row.get('away_trend', 0) >= 0:
        conf = pa * 10
        if row.get('away_losestreak', 0) >= 3:  conf *= 1.25
        elif row.get('away_losestreak', 0) >= 2: conf *= 1.10
        if row.get('home_winstreak', 0) >= 2:   conf *= 1.15
        if row.get('away_gf5', 0) > 1.8 and row.get('away_ga5', 0) < 1.5: conf *= 1.20
        pin_odds = row.get('odds_a')
        if pin_odds and pin_odds > 0:
            return ('A', round(conf, 1), float(pin_odds),
                    f"pa={pa:.0%} vg={vg} pd5={pd5}")

    return None


def score_ht(row):
    """Returns (pick, conf, est_odds, reason) or None."""
    ph = row.get('prob_h', 0) or 0
    pa = row.get('prob_a', 0) or 0
    h_lead = row.get('h_ht_lead_rate', 0) or 0
    a_lead = row.get('a_ht_lead_rate', 0) or 0

    # HT HOME
    if ph >= 0.68 and h_lead >= 0.40:
        score = ph * 0.78 * 10
        if h_lead >= 0.50: score *= 1.25
        if a_lead < 0.15:  score *= 1.15
        if ph >= 0.75:     score *= 1.15
        # Estimate odds: if ph=0.70, HT-H prob≈0.70*0.78=0.546 → odds≈1/0.546≈1.83
        # Winner typically adds margin, so use Pinnacle FT home odds * 0.78 calibration
        pin_h = row.get('pinnacle_h') or 0
        est_odds = round(1 / (ph * 0.78), 2) if ph > 0 else 1.80
        if score >= 5:
            return ('H', round(score, 1), est_odds,
                    f"ph={ph:.0%} home_ht_lead={h_lead:.0%}")

    # HT AWAY
    if pa >= 0.62 and a_lead >= 0.35:
        score = pa * 0.78 * 10
        if a_lead >= 0.45: score *= 1.25
        if h_lead < 0.20:  score *= 1.15
        if pa >= 0.70:     score *= 1.15
        est_odds = round(1 / (pa * 0.78), 2) if pa > 0 else 2.10
        if score >= 5:
            return ('A', round(score, 1), est_odds,
                    f"pa={pa:.0%} away_ht_lead={a_lead:.0%}")

    return None


def score_corner(row):
    """Returns (pick, conf, est_odds, reason) or None. Odds ~1.80 for H/A first corner."""
    exp_h = row.get('exp_first_home', 0.5) or 0.5

    if exp_h > 0.65:
        score = (exp_h - 0.50) * 20
        if score >= 4:
            return ('H', round(score, 1), 1.80,
                    f"home generates {exp_h:.0%} of expected corners")

    if exp_h < 0.35:
        score = (0.50 - exp_h) * 20
        if score >= 4:
            return ('A', round(score, 1), 1.80,
                    f"away generates {1-exp_h:.0%} of expected corners")

    return None


def score_draw(row):
    """Returns (pick, conf, est_odds) or None."""
    pd_ = row.get('prob_d', 0) or 0
    gap = abs(row.get('pts5_diff', 99) or 99)
    hdr = row.get('home_dr10', 0) or 0
    adr = row.get('away_dr10', 0) or 0
    draw_odds = row.get('odds_d') or 0

    if pd_ >= 0.29 and gap <= 1 and hdr > 0.20 and adr > 0.20:
        return ('D', pd_ * 100, float(draw_odds) if draw_odds else 3.40,
                f"pd={pd_:.0%} gap={gap}")
    return None


# ── Combined slip backtest ─────────────────────────────────────────────────────

def run_combined_backtest(n_weeks: int = 20) -> None:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT league, date, home_team, away_team,
               home_goals, away_goals, result,
               ht_result, ht_home_goals, ht_away_goals,
               home_corners, away_corners,
               pinnacle_h, pinnacle_d, pinnacle_a,
               pinnacle_prob_h, pinnacle_prob_d, pinnacle_prob_a,
               avg_h, avg_d, avg_a,
               avg_prob_h, avg_prob_d, avg_prob_a
        FROM matches_history
        WHERE result IS NOT NULL AND result != ''
          AND (pinnacle_prob_h IS NOT NULL OR avg_prob_h IS NOT NULL)
        ORDER BY date
    """, conn)
    conn.close()

    # Use Pinnacle where available, fall back to avg market odds
    df['prob_h'] = df['pinnacle_prob_h'].fillna(df['avg_prob_h'])
    df['prob_d'] = df['pinnacle_prob_d'].fillna(df['avg_prob_d'])
    df['prob_a'] = df['pinnacle_prob_a'].fillna(df['avg_prob_a'])
    df['odds_h'] = df['pinnacle_h'].fillna(df['avg_h'])
    df['odds_d'] = df['pinnacle_d'].fillna(df['avg_d'])
    df['odds_a'] = df['pinnacle_a'].fillna(df['avg_a'])
    df['odds_source'] = df['pinnacle_prob_h'].apply(lambda x: 'pinnacle' if pd.notna(x) else 'avg')

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # Build features per league
    league_dfs = []
    for league, ldf in df.groupby('league'):
        league_dfs.append(build_features(ldf.sort_values('date').reset_index(drop=True)))
    df = pd.concat(league_dfs).sort_values('date').reset_index(drop=True)

    # Score every match
    ha_scored  = df.apply(score_ha,     axis=1)
    ht_scored  = df.apply(score_ht,     axis=1)
    crn_scored = df.apply(score_corner, axis=1)
    drw_scored = df.apply(score_draw,   axis=1)

    df['ha_res']  = ha_scored
    df['ht_res']  = ht_scored
    df['crn_res'] = crn_scored
    df['drw_res'] = drw_scored

    df['week'] = df['date'].dt.to_period('W')

    # Get last N qualifying weeks from the current season only (Aug 2025+)
    all_weeks = sorted(df['week'].unique())
    current_season = [w for w in all_weeks if str(w) >= '2025-08']
    weeks = current_season[-n_weeks:] if n_weeks < len(current_season) else current_season

    rows = []
    slip_results  = []  # (won, combined_odds)
    draw_results  = []  # (won, odds)

    for week in weeks:
        wdf = df[df['week'] == week].copy()

        # ── Collect all candidates per market (sorted best first) ────────────
        def match_id(r): return f"{r['home_team']}|{r['away_team']}"

        ha_cands  = sorted(
            [(r['ha_res'], r) for _, r in wdf.iterrows() if r['ha_res'] is not None],
            key=lambda x: x[0][1], reverse=True)
        ht_cands  = sorted(
            [(r['ht_res'], r) for _, r in wdf.iterrows()
             if r['ht_res'] is not None and r['ht_res'][1] >= 10],
            key=lambda x: x[0][1], reverse=True)
        crn_cands = sorted(
            [(r['crn_res'], r) for _, r in wdf.iterrows() if r['crn_res'] is not None],
            key=lambda x: x[0][1], reverse=True)

        # ── Draw: best pick ────────────────────────────────────────────────
        drw_cands = [(r['drw_res'], r) for _, r in wdf.iterrows() if r['drw_res'] is not None]
        best_drw = max(drw_cands, key=lambda x: x[0][0], default=None)  # rank by pd

        # ── Build combined slip: each leg must be from a different match ───
        # Greedy: pick best H/A first, then best HT not from same match,
        # then best corner not from same matches already used.
        slip_legs = []
        slip_odds = 1.0
        used_matches = set()

        def add_leg(market, cands, correct_fn):
            nonlocal slip_odds
            for res, row_r in cands:
                mid = match_id(row_r)
                if mid in used_matches:
                    continue
                pick, conf, odds, reason = res
                correct = correct_fn(pick, row_r)
                slip_legs.append({
                    'market': market,
                    'match': f"{row_r['home_team']} vs {row_r['away_team']}",
                    'pick': pick, 'conf': conf, 'odds': odds,
                    'correct': correct, 'reason': reason,
                })
                slip_odds *= odds
                used_matches.add(mid)
                return True
            return False

        add_leg('H/A', ha_cands,
                lambda pick, r: (pick == 'H' and r['actual_result'] == 'H') or
                                (pick == 'A' and r['actual_result'] == 'A'))
        add_leg('1st CRN', crn_cands,
                lambda pick, r: pick == r['actual_first_corner'])

        # HT only added when confidence is very high (≥14) — bonus 3rd leg
        ht_high = [(res, r) for res, r in ht_cands if res[1] >= 14]
        add_leg('HT', ht_high,
                lambda pick, r: pick == r['actual_ht_result'])

        # ── Determine combined slip result ─────────────────────────────────
        all_correct = all(l['correct'] for l in slip_legs) if slip_legs else None
        n_legs = len(slip_legs)

        # Draw result
        draw_correct = None
        draw_odds    = None
        draw_match   = None
        draw_pick    = None
        if best_drw:
            pick, conf, odds, reason = best_drw[0]
            row_r = best_drw[1]
            draw_correct = (row_r['actual_result'] == 'D')
            draw_odds    = odds
            draw_match   = f"{row_r['home_team']} vs {row_r['away_team']}"
            draw_pick    = pick

        # Flatten to one row per week with per-leg columns (up to 3 legs)
        odds_sources = wdf['odds_source'].unique().tolist()
        row_out = {
            'week':          str(week),
            'odds_source':   'avg' if all(s == 'avg' for s in odds_sources) else 'pinnacle',
            'slip_won':      all_correct,
            'combined_odds': round(slip_odds, 2) if slip_legs else None,
            'n_legs':        n_legs,
        }
        for i, leg in enumerate(slip_legs, 1):
            row_out[f'leg{i}_market'] = leg['market']
            row_out[f'leg{i}_match']  = leg['match']
            row_out[f'leg{i}_pick']   = leg['pick']
            row_out[f'leg{i}_odds']   = round(leg['odds'], 2)
            row_out[f'leg{i}_why']    = leg['reason']
            row_out[f'leg{i}_hit']    = leg['correct']
        for i in range(len(slip_legs) + 1, 4):  # pad missing legs
            row_out[f'leg{i}_market'] = ''
            row_out[f'leg{i}_match']  = ''
            row_out[f'leg{i}_pick']   = ''
            row_out[f'leg{i}_odds']   = ''
            row_out[f'leg{i}_why']    = ''
            row_out[f'leg{i}_hit']    = ''
        row_out['draw_match']   = draw_match or ''
        row_out['draw_pick']    = draw_pick or ''
        row_out['draw_odds']    = round(draw_odds, 2) if draw_odds else ''
        row_out['draw_hit']     = draw_correct if draw_correct is not None else ''
        rows.append(row_out)

        if slip_legs:
            slip_results.append({'won': all_correct, 'odds': slip_odds, 'legs': n_legs,
                                 'slip_legs': slip_legs})
        if best_drw:
            draw_results.append({'won': draw_correct, 'odds': draw_odds})

    # ── Print results ──────────────────────────────────────────────────────────
    print(f"\n{'='*90}")
    print(f"  COMBINED MULTI-MARKET SLIP — last {n_weeks} weeks")
    print(f"{'='*90}")
    print(f"{'Week':<22} {'Src':<4} {'Legs':<5} {'Combined odds':<14} {'Legs result':<30} {'Draw':<6}")
    print(f"{'-'*94}")

    for r in rows:
        leg_icons = '/'.join(
            ('✅' if r[f'leg{i}_hit'] else '❌')
            for i in range(1, r['n_legs'] + 1)
        ) if r['n_legs'] else '—'
        slip_disp = (f"✅ {r['combined_odds']:.2f}x" if r['slip_won'] is True
                     else (f"❌ {r['combined_odds']:.2f}x" if r['slip_won'] is False else '—'))
        draw_disp = ('✅' if r['draw_hit'] is True else ('❌' if r['draw_hit'] is False else '—'))
        src = '📊' if r['odds_source'] == 'avg' else '📌'
        print(f"{r['week']:<22} {src:<4} {r['n_legs']:<5} {slip_disp:<14} {leg_icons:<30} {draw_disp}")

    print(f"\n{'─'*90}")

    # ── Stats ──────────────────────────────────────────────────────────────────
    if slip_results:
        n_slip = len(slip_results)
        w_slip = sum(1 for r in slip_results if r['won'])
        avg_o  = np.mean([r['odds'] for r in slip_results])
        ev     = (w_slip / n_slip) * avg_o
        print(f"\nCombined slip:  {w_slip}/{n_slip} = {w_slip/n_slip:.0%} win rate | "
              f"avg odds {avg_o:.2f}x | EV {ev:.3f}")

        # By number of legs
        for n in [1, 2, 3]:
            sub = [r for r in slip_results if r['legs'] == n]
            if sub:
                w = sum(1 for r in sub if r['won'])
                ao = np.mean([r['odds'] for r in sub])
                ev_n = (w/len(sub)) * ao
                print(f"  {n}-leg slips:  {w}/{len(sub)} = {w/len(sub):.0%} | avg {ao:.2f}x | EV {ev_n:.3f}")

    if draw_results:
        n_d = len(draw_results)
        w_d = sum(1 for r in draw_results if r['won'])
        ao_d = np.mean([r['odds'] for r in draw_results])
        ev_d = (w_d / n_d) * ao_d
        print(f"Draw single:    {w_d}/{n_d} = {w_d/n_d:.0%} win rate | "
              f"avg odds {ao_d:.2f}x | EV {ev_d:.3f}")

    # ── Save CSV ───────────────────────────────────────────────────────────────
    out = pd.DataFrame(rows)
    out_path = REPORT_DIR / "combined_slip_last20.csv"
    out.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    # ── Also show leg breakdown for clarity ───────────────────────────────────
    print(f"\n{'─'*90}")
    print("  DETAIL: what each leg was")
    print(f"{'─'*90}")
    for r in rows:
        if r['n_legs']:
            print(f"\n  {r['week']}  → slip {'✅ WON' if r['slip_won'] else '❌ LOST'} @ {r['combined_odds']}x")
            for i in range(1, r['n_legs'] + 1):
                hit = '✅' if r[f'leg{i}_hit'] else '❌'
                print(f"    {hit} [{r[f'leg{i}_market']}] {r[f'leg{i}_match']} — pick {r[f'leg{i}_pick']} "
                      f"@ {r[f'leg{i}_odds']}x | {r[f'leg{i}_why']}")
            if r['draw_match']:
                dhit = '✅' if r['draw_hit'] is True else ('❌' if r['draw_hit'] is False else '—')
                print(f"    {dhit} [DRAW]  {r['draw_match']} @ {r['draw_odds']}x")


if __name__ == "__main__":
    run_combined_backtest(n_weeks=999)  # show full current season
