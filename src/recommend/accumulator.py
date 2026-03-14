"""
Accumulator builder — versioned.
Each version is saved as accumulator_backtest_vN.csv and logged in VERSIONS.md
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

UNRELIABLE_HOME = {'Tottenham', 'Man United', 'Chelsea', 'Brighton', 'West Ham', 'Bournemouth'}


# ── Feature builder ───────────────────────────────────────────────────────────

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    # Separate histories: all games + home-only + away-only
    team_all   = {}  # all results
    team_home  = {}  # home games only
    team_away  = {}  # away games only

    rows_out = []
    for _, row in df.iterrows():
        def pts(hist, n):
            h = hist[-n:]
            return sum(3 if r == 'W' else 1 if r == 'D' else 0 for _, _, _, r in h)
        def gf(hist, n):
            h = hist[-n:]
            return sum(g for _, g, _, _ in h) / max(len(h), 1)
        def ga(hist, n):
            h = hist[-n:]
            return sum(g for _, _, g, _ in h) / max(len(h), 1)
        def dr(hist, n):
            h = hist[-n:]
            return sum(1 for _, _, _, r in h if r == 'D') / max(len(h), 1)
        def trend(hist):
            # Points in last 3 minus points in previous 3 → positive = improving
            recent = pts(hist, 3)
            prior  = pts(hist[-6:-3], 3) if len(hist) >= 6 else pts(hist, 3)
            return recent - prior
        def winstreak(hist):
            # Current consecutive wins
            streak = 0
            for entry in reversed(hist):
                if entry[3] == 'W':
                    streak += 1
                else:
                    break
            return streak
        def losestreak(hist):
            streak = 0
            for entry in reversed(hist):
                if entry[3] == 'L':
                    streak += 1
                else:
                    break
            return streak

        ht, at = row['home_team'], row['away_team']
        h_all  = team_all.get(ht, [])
        a_all  = team_all.get(at, [])
        h_home = team_home.get(ht, [])  # home team's home games
        a_away = team_away.get(at, [])  # away team's away games

        r2 = row.copy()

        # Overall form
        r2['home_pts5']   = pts(h_all, 5);   r2['away_pts5']   = pts(a_all, 5)
        r2['home_pts10']  = pts(h_all, 10);  r2['away_pts10']  = pts(a_all, 10)
        r2['home_gf5']    = gf(h_all, 5);    r2['home_ga5']    = ga(h_all, 5)
        r2['away_gf5']    = gf(a_all, 5);    r2['away_ga5']    = ga(a_all, 5)
        r2['pts5_diff']   = r2['home_pts5']  - r2['away_pts5']
        r2['pts10_diff']  = r2['home_pts10'] - r2['away_pts10']
        r2['home_dr10']   = dr(h_all, 10);   r2['away_dr10']   = dr(a_all, 10)

        # Venue-specific form (NEW)
        r2['home_venue_pts5']  = pts(h_home, 5)   # home team at home last 5
        r2['away_venue_pts5']  = pts(a_away, 5)   # away team away last 5
        r2['venue_gap']        = r2['home_venue_pts5'] - r2['away_venue_pts5']

        # Trend: improving or declining? (NEW)
        r2['home_trend']  = trend(h_all)   # +ve = team getting better
        r2['away_trend']  = trend(a_all)
        r2['trend_diff']  = r2['home_trend'] - r2['away_trend']

        # Win/loss streaks (NEW)
        r2['home_winstreak']  = winstreak(h_all)
        r2['away_winstreak']  = winstreak(a_all)
        r2['home_losestreak'] = losestreak(h_all)
        r2['away_losestreak'] = losestreak(a_all)

        hg, ag = int(row['home_goals'] or 0), int(row['away_goals'] or 0)
        h_res = 'W' if hg > ag else 'D' if hg == ag else 'L'
        a_res = 'W' if ag > hg else 'D' if ag == hg else 'L'

        team_all.setdefault(ht,  []).append((row['date'], hg, ag, h_res))
        team_all.setdefault(at,  []).append((row['date'], ag, hg, a_res))
        team_home.setdefault(ht, []).append((row['date'], hg, ag, h_res))  # home games
        team_away.setdefault(at, []).append((row['date'], ag, hg, a_res))  # away games

        rows_out.append(r2)
    return pd.DataFrame(rows_out)


# ── Scoring functions (one per version) ──────────────────────────────────────

def scorer_v1(row):
    """V1: H/A only, basic odds+form filter."""
    ph, pa, pd_ = row['pinnacle_prob_h'], row['pinnacle_prob_a'], row['pinnacle_prob_d']
    gap = row['pts5_diff']
    combined_dr = (row['home_dr10'] + row['away_dr10']) / 2
    if pd_ > 0.26: return None, 0, None
    if combined_dr > 0.30: return None, 0, None
    if ph >= 0.65 and gap >= 6:
        if row['home_team'] in UNRELIABLE_HOME and ph < 0.75: return None, 0, None
        score = ph * 10 * (1 + gap / 30) * (1 - combined_dr)
        if row['home_pts5'] >= 12 and row['away_pts5'] <= 3: score *= 2.0
        return ('H', score, row['pinnacle_h']) if score >= 6 else (None, 0, None)
    if pa >= 0.60 and gap <= -6:
        score = pa * 10 * (1 + abs(gap) / 30) * (1 - combined_dr)
        return ('A', score, row['pinnacle_a']) if score >= 5 else (None, 0, None)
    return None, 0, None


def scorer_v2(row):
    """V2: adds DRAW picks when conditions are met. Draw boosted to compete."""
    ph, pa, pd_ = row['pinnacle_prob_h'], row['pinnacle_prob_a'], row['pinnacle_prob_d']
    gap = row['pts5_diff']
    hdr, adr = row['home_dr10'], row['away_dr10']
    combined_dr = (hdr + adr) / 2

    candidates = []

    # DRAW: Pinnacle draw>28% + tight form + both teams draw-prone
    # 33% actual rate at 3.36 odds → EV 1.11
    if pd_ > 0.28 and abs(gap) <= 3 and hdr > 0.20 and adr > 0.20:
        # Boost so draw can compete against low-odds H/A picks
        draw_conf = pd_ * 15 * (1 + combined_dr * 2) * (1 / (1 + abs(gap) * 0.05))
        if pd_ > 0.32: draw_conf *= 1.4   # strongest draw signal
        candidates.append(('D', draw_conf, row['pinnacle_d']))

    # HOME: strong favourite + form gap + not unreliable team
    if ph >= 0.65 and gap >= 6:
        if not (row['home_team'] in UNRELIABLE_HOME and ph < 0.75):
            h_conf = ph * 10 * (1 + gap / 30) * (1 - combined_dr * 0.5)
            if row['home_pts5'] >= 12 and row['away_pts5'] <= 3: h_conf *= 2.0
            if row['home_gf5'] > 1.8 and row['away_ga5'] > 1.5:  h_conf *= 1.3
            candidates.append(('H', h_conf, row['pinnacle_h']))

    # AWAY: strong away fav + form gap
    if pa >= 0.60 and gap <= -6:
        a_conf = pa * 10 * (1 + abs(gap) / 30) * (1 - combined_dr * 0.5)
        if row['away_pts5'] >= 12 and row['home_pts5'] <= 3: a_conf *= 2.0
        candidates.append(('A', a_conf, row['pinnacle_a']))

    if not candidates: return None, 0, None
    best = max(candidates, key=lambda x: x[1])
    return best if best[1] >= 5 else (None, 0, None)


def scorer_v3(row):
    """
    V3: H/A only in accumulator (draws excluded — they kill accumulators).
    Primary gates use VENUE form (home team's home record vs away team's away record),
    plus trend filter (not picking declining teams) and streak bonuses.
    """
    ph, pa = row['pinnacle_prob_h'], row['pinnacle_prob_a']

    # Venue-specific gap: home-team's home pts last 5 vs away-team's away pts last 5
    venue_gap  = row.get('venue_gap', row.get('pts5_diff', 0))
    pts5_diff  = row.get('pts5_diff', 0)

    # Trend: positive = improving, negative = declining
    h_trend = row.get('home_trend', 0)
    a_trend = row.get('away_trend', 0)

    # Streaks
    h_win  = row.get('home_winstreak', 0)
    a_lose = row.get('away_losestreak', 0)
    a_win  = row.get('away_winstreak', 0)
    h_lose = row.get('home_losestreak', 0)

    combined_dr = (row['home_dr10'] + row['away_dr10']) / 2

    # ── HOME pick ────────────────────────────────────────────────────────────
    # Need: strong Pinnacle favour + venue form edge + home not declining
    if ph >= 0.63 and venue_gap >= 5 and pts5_diff >= 5 and h_trend >= 0:
        if row['home_team'] in UNRELIABLE_HOME and ph < 0.72:
            pass  # skip unreliable teams unless heavily favoured
        else:
            score = ph * 10 * (1 + venue_gap / 25)
            # Streak bonus
            if h_win >= 3:  score *= 1.25
            elif h_win >= 2: score *= 1.10
            if a_lose >= 2:  score *= 1.15
            # Attacking mismatch bonus
            if row.get('home_gf5', 0) > 1.8 and row.get('away_ga5', 0) > 1.5:
                score *= 1.20
            # Form dominance bonus
            if row.get('home_pts5', 0) >= 12 and row.get('away_pts5', 0) <= 3:
                score *= 1.50
            # Penalise if home trend is only flat (not actually improving)
            if h_trend == 0 and a_trend > 1:
                score *= 0.85
            if score >= 7:
                return ('H', score, row['pinnacle_h'])

    # ── AWAY pick ────────────────────────────────────────────────────────────
    # Need: strong away Pinnacle favour + venue form edge (away better away than home is at home)
    if pa >= 0.58 and venue_gap <= -5 and pts5_diff <= -5 and a_trend >= 0:
        score = pa * 10 * (1 + abs(venue_gap) / 25)
        if a_win >= 3:  score *= 1.25
        elif a_win >= 2: score *= 1.10
        if h_lose >= 2:  score *= 1.15
        if row.get('away_gf5', 0) > 1.8 and row.get('home_ga5', 0) > 1.5:
            score *= 1.20
        if row.get('away_pts5', 0) >= 12 and row.get('home_pts5', 0) <= 3:
            score *= 1.50
        if score >= 6:
            return ('A', score, row['pinnacle_a'])

    return None, 0, None


def scorer_v4(row):
    """V4: Same H/A logic as V3. Draws are handled separately as singles."""
    return scorer_v3(row)


def scorer_v5(row):
    """V5: Same H/A logic as V3. Improved draw singles (separate scorer)."""
    return scorer_v3(row)


def draw_scorer_v4(row):
    """
    Improved draw scorer — designed to pick the SINGLE best draw per week.
    Signals used:
      • Pinnacle draw prob (primary market signal)
      • Form parity — the tighter the gap, the more likely a draw
      • Both teams draw-prone (draw rate last 10)
      • Low-scoring teams — both avg < 1.5 goals scored → 0-0 / 1-1 territory
      • Defensive equilibrium — both teams concede similarly
      • Away draw tendency — away teams draw more than they win on the road
    Returns (confidence_score, odds) or (0, None).
    """
    pd_ = row['pinnacle_prob_d']
    gap = row.get('pts5_diff', 0)
    hdr = row.get('home_dr10', 0)
    adr = row.get('away_dr10', 0)
    hgf = row.get('home_gf5', 0)   # home team goals scored per game last 5
    agf = row.get('away_gf5', 0)   # away team goals scored per game last 5
    hga = row.get('home_ga5', 0)   # home team goals conceded per game last 5
    aga = row.get('away_ga5', 0)   # away team goals conceded per game last 5

    # Gate: must have Pinnacle signal + roughly equal form
    if pd_ < 0.26 or abs(gap) > 5:
        return 0, None

    # Base: Pinnacle draw probability (sharpest signal)
    score = pd_ * 10

    # Form parity — gap of 0 is ideal, penalise as gap widens
    parity_factor = 1 / (1 + abs(gap) * 0.15)
    score *= (1 + parity_factor)

    # Draw propensity — both teams must have drawn recently
    draw_pool = (hdr + adr) / 2
    if draw_pool < 0.18:
        return 0, None   # neither team is draw-prone → skip
    score *= (1 + draw_pool * 2)

    # Low-scoring game bonus — tight, low-goal matches end in draws more often
    avg_goals_scored = (hgf + agf) / 2
    if avg_goals_scored < 1.2:
        score *= 1.40    # very defensive teams
    elif avg_goals_scored < 1.5:
        score *= 1.20

    # Defensive equilibrium — if both teams concede roughly the same amount,
    # neither has a clear attacking edge → draw-friendly
    if hga > 0 and aga > 0:
        def_ratio = min(hga, aga) / max(hga, aga)
        score *= (1 + def_ratio * 0.3)   # up to +30% when perfectly balanced

    # Strong Pinnacle draw signal bonus
    if pd_ > 0.33:
        score *= 1.30
    elif pd_ > 0.30:
        score *= 1.10

    # Trend: if both teams are in neutral/flat form neither pushing, draw more likely
    h_trend = row.get('home_trend', 0)
    a_trend = row.get('away_trend', 0)
    if h_trend == 0 and a_trend == 0:
        score *= 1.10

    return score, row['pinnacle_d']


def draw_scorer_v5(row):
    """
    V5 draw scorer — simpler, stricter gates, Pinnacle-first ranking.
    Key insight from V2: pd_>0.28 + gap≤3 + both dr>0.20 → 32% accuracy at 3.35.
    V5 strategy: tighten all gates, score = Pinnacle draw prob × combined draw rate.
    Pick the 1 highest-confidence draw per week only.
    """
    pd_ = row['pinnacle_prob_d']
    gap = row.get('pts5_diff', 0)
    hdr = row.get('home_dr10', 0)
    adr = row.get('away_dr10', 0)
    hgf = row.get('home_gf5', 0)
    agf = row.get('away_gf5', 0)

    # Strict gates — Pinnacle must be signalling draw AND form must be tight
    if pd_ < 0.28:      return 0, None   # Pinnacle doesn't favour draw
    if abs(gap) > 3:    return 0, None   # teams not evenly matched
    if hdr < 0.20:      return 0, None   # home team not draw-prone
    if adr < 0.20:      return 0, None   # away team not draw-prone

    # Score: Pinnacle signal (primary) × draw pool (both teams tendency)
    # Pinnacle is already the sharpest predictor — don't dilute it with noise
    draw_pool = (hdr + adr) / 2
    score = pd_ * draw_pool * 100         # e.g. 0.31 × 0.28 × 100 = 8.7

    # Small bonus: extra strong Pinnacle signal
    if pd_ > 0.32:   score *= 1.20
    elif pd_ > 0.30: score *= 1.10

    # Small bonus: perfectly balanced form (gap = 0)
    if gap == 0:     score *= 1.10

    # Small bonus: both teams scoring < 1.4 goals/game → tight defensive game
    if hgf < 1.4 and agf < 1.4:
        score *= 1.10

    return score, row['pinnacle_d']


# Add new versions here as we iterate
SCORERS = {
    'v1': scorer_v1,
    'v2': scorer_v2,
    'v3': scorer_v3,
    'v4': scorer_v4,
    'v5': scorer_v5,
}

DRAW_SCORERS = {
    'v4': draw_scorer_v4,
    'v5': draw_scorer_v5,
}


# ── Explanation builder ───────────────────────────────────────────────────────

def _explain(r) -> str:
    pick = r['pick']
    ph   = r['pinnacle_prob_h']
    pd_  = r['pinnacle_prob_d']
    pa   = r['pinnacle_prob_a']
    gap  = r.get('pts5_diff', 0)
    hpts = r.get('home_pts5', 0)
    apts = r.get('away_pts5', 0)
    hdr  = r.get('home_dr10', 0)
    adr  = r.get('away_dr10', 0)
    hgf  = r.get('home_gf5', 0)
    aga  = r.get('away_ga5', 0)

    if pick == 'H':
        parts = [f"Pinnacle gives home {ph:.0%} win prob"]
        if gap >= 9:
            parts.append(f"dominant form gap: {hpts:.0f}pts vs {apts:.0f}pts in last 5 (historic 87% accuracy)")
        elif gap >= 6:
            parts.append(f"clear form advantage: {hpts:.0f}pts vs {apts:.0f}pts in last 5")
        if hpts >= 12 and apts <= 3:
            parts.append(f"MISMATCH: home on {hpts:.0f}pts, away on {apts:.0f}pts — best config (90%+ historic)")
        if hgf > 1.8 and aga > 1.5:
            parts.append(f"attacking mismatch: home scores {hgf:.1f}/game, opp concedes {aga:.1f}/game")
        venue_gap = r.get('venue_gap', None)
        if venue_gap is not None:
            parts.append(f"venue form gap: {venue_gap:+.0f} (home-at-home vs away-at-away)")
        h_win = r.get('home_winstreak', 0)
        a_lose = r.get('away_losestreak', 0)
        if h_win >= 2: parts.append(f"home on {h_win}-game win streak")
        if a_lose >= 2: parts.append(f"away on {a_lose}-game losing streak")
        return " | ".join(parts)

    elif pick == 'A':
        parts = [f"Pinnacle gives away {pa:.0%} win prob"]
        if gap <= -9:
            parts.append(f"dominant away form gap: {apts:.0f}pts vs {hpts:.0f}pts in last 5 (historic 87%)")
        elif gap <= -6:
            parts.append(f"away team clearly stronger: {apts:.0f}pts vs {hpts:.0f}pts in last 5")
        agf = r.get('away_gf5', 0)
        hga = r.get('home_ga5', 0)
        if agf > 1.8 and hga > 1.5:
            parts.append(f"away attack {agf:.1f}/game vs leaky home defence {hga:.1f} conceded/game")
        venue_gap = r.get('venue_gap', None)
        if venue_gap is not None:
            parts.append(f"venue form gap: {venue_gap:+.0f} (home-at-home vs away-at-away)")
        a_win = r.get('away_winstreak', 0)
        h_lose = r.get('home_losestreak', 0)
        if a_win >= 2: parts.append(f"away on {a_win}-game win streak")
        if h_lose >= 2: parts.append(f"home on {h_lose}-game losing streak")
        return " | ".join(parts)

    elif pick == 'D':
        parts = [f"Pinnacle draw prob {pd_:.0%} (odds {r['pinnacle_d']:.2f})"]
        parts.append(f"tight form: {hpts:.0f}pts vs {apts:.0f}pts, gap={gap:+.0f}")
        parts.append(f"both teams draw-prone: home {hdr:.0%}, away {adr:.0%} draw rate last 10")
        return " | ".join(parts)

    return ""


# ── Backtest runner ───────────────────────────────────────────────────────────

def run_backtest(version: str = 'v2', max_legs: int = 3, min_legs: int = 2) -> dict:
    scorer      = SCORERS[version]
    draw_scorer = DRAW_SCORERS.get(version)

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT league, date, home_team, away_team, result,
               pinnacle_prob_h, pinnacle_prob_d, pinnacle_prob_a,
               pinnacle_h, pinnacle_d, pinnacle_a, home_goals, away_goals
        FROM matches_history
        WHERE pinnacle_prob_h IS NOT NULL
        ORDER BY date
    """, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # Build features per league so form stats don't bleed across leagues
    league_dfs = []
    for league, ldf in df.groupby('league'):
        ldf = ldf.sort_values('date').reset_index(drop=True)
        league_dfs.append(build_features(ldf))
    df = pd.concat(league_dfs).sort_values('date').reset_index(drop=True)

    df['pick'], df['conf'], df['bet_odds'] = zip(*df.apply(scorer, axis=1))
    df_picks = df[df['pick'].notna()].copy()
    df_picks['week'] = df_picks['date'].dt.to_period('W')

    # ── Draw singles (v4+) — 1 best pick per week ────────────────────────────
    if draw_scorer:
        draw_results = df.apply(draw_scorer, axis=1)
        df['draw_conf'] = draw_results.apply(lambda x: x[0])
        df['draw_odds'] = draw_results.apply(lambda x: x[1])
        df_draws = df[df['draw_conf'] > 0].copy()
        df_draws['week'] = df_draws['date'].dt.to_period('W')

        draw_rows = []
        draw_rows_all = []   # all qualifying (for comparison)

        # All qualifying draws
        for _, r in df_draws.iterrows():
            correct = r['result'] == 'D'
            draw_rows_all.append({
                'week':       str(r['date'].to_period('W')),
                'date':       str(r['date'])[:10],
                'match':      f"{r['home_team']} vs {r['away_team']}",
                'actual':     r['result'],
                'odds':       round(r['draw_odds'], 2),
                'correct':    correct,
                'confidence': round(r['draw_conf'], 1),
                'pin_prob_d': round(r['pinnacle_prob_d'], 2),
                'home_gf5':   round(r.get('home_gf5', 0), 2),
                'away_gf5':   round(r.get('away_gf5', 0), 2),
                'form_gap':   round(r.get('pts5_diff', 0), 1),
                'home_dr10':  round(r.get('home_dr10', 0), 2),
                'away_dr10':  round(r.get('away_dr10', 0), 2),
            })

        # Best 1 per week (highest confidence draw)
        for week, wgrp in df_draws.groupby('week'):
            best = wgrp.sort_values('draw_conf', ascending=False).iloc[0]
            correct = best['result'] == 'D'
            draw_rows.append({
                'week':       str(week),
                'date':       str(best['date'])[:10],
                'match':      f"{best['home_team']} vs {best['away_team']}",
                'actual':     best['result'],
                'odds':       round(best['draw_odds'], 2),
                'correct':    correct,
                'confidence': round(best['draw_conf'], 1),
                'pin_prob_d': round(best['pinnacle_prob_d'], 2),
                'home_gf5':   round(best.get('home_gf5', 0), 2),
                'away_gf5':   round(best.get('away_gf5', 0), 2),
                'form_gap':   round(best.get('pts5_diff', 0), 1),
                'home_dr10':  round(best.get('home_dr10', 0), 2),
                'away_dr10':  round(best.get('away_dr10', 0), 2),
            })

        if draw_rows:
            draw_df = pd.DataFrame(draw_rows).sort_values('week').reset_index(drop=True)
            draw_path = REPORT_DIR / f"draw_singles_{version}.csv"
            draw_df.to_csv(draw_path, index=False)

            draw_all_df = pd.DataFrame(draw_rows_all).sort_values(['week', 'confidence'], ascending=[True, False]).reset_index(drop=True)
            draw_all_path = REPORT_DIR / f"draw_singles_{version}_all.csv"
            draw_all_df.to_csv(draw_all_path, index=False)

            acc_weekly = draw_df['correct'].mean()
            acc_all    = draw_all_df['correct'].mean()
            avg_o      = draw_df['odds'].mean()
            ev_weekly  = acc_weekly * avg_o
            ev_all     = draw_all_df['correct'].mean() * draw_all_df['odds'].mean()

            print(f"\n── Draw singles ──────────────────────────────────────────")
            print(f"  Best 1/week:   {len(draw_df)} weeks | {acc_weekly:.1%} accuracy | {avg_o:.2f} avg odds | EV {ev_weekly:.3f}")
            print(f"  All qualifying:{len(draw_all_df)} bets  | {acc_all:.1%} accuracy | {draw_all_df['odds'].mean():.2f} avg odds | EV {ev_all:.3f}")
            print(f"  Saved: {draw_path}")

    csv_rows = []
    results  = []

    for week, grp in df_picks.groupby('week'):
        grp  = grp.sort_values('conf', ascending=False)
        top  = grp.head(max_legs)
        if len(top) < min_legs:
            continue

        all_correct = True
        combo_odds  = 1.0
        legs        = []

        for _, r in top.iterrows():
            correct     = r['result'] == r['pick']
            all_correct = all_correct and correct
            combo_odds *= r['bet_odds']
            legs.append({
                'week':          str(week),
                'match':         f"{r['home_team']} vs {r['away_team']}",
                'pick':          r['pick'],
                'pick_label':    {'H': 'Home Win', 'D': 'Draw', 'A': 'Away Win'}[r['pick']],
                'actual':        r['result'],
                'odds_h':        round(r['pinnacle_h'], 2),
                'odds_d':        round(r['pinnacle_d'], 2),
                'odds_a':        round(r['pinnacle_a'], 2),
                'odds_picked':   round(r['bet_odds'], 2),
                'correct':       correct,
                'confidence':    round(r['conf'], 1),
                'home_form5':    r.get('home_pts5', ''),
                'away_form5':    r.get('away_pts5', ''),
                'form_gap':      round(r.get('pts5_diff', 0), 1),
                'home_draw_rate': round(r.get('home_dr10', 0), 2),
                'away_draw_rate': round(r.get('away_dr10', 0), 2),
                'pin_prob_h':    round(r['pinnacle_prob_h'], 2),
                'pin_prob_d':    round(r['pinnacle_prob_d'], 2),
                'pin_prob_a':    round(r['pinnacle_prob_a'], 2),
                'reason':        _explain(r),
            })

        for L in legs:
            L['week_won']   = all_correct
            L['combo_odds'] = round(combo_odds, 2)
            csv_rows.append(L)

        results.append({
            'week': str(week), 'legs': legs,
            'all_correct': all_correct,
            'combo_odds':  round(combo_odds, 2),
            'n':           len(legs),
        })

    # Save CSV
    out_path = REPORT_DIR / f"accumulator_backtest_{version}.csv"
    out_df   = pd.DataFrame(csv_rows, columns=[
        'week', 'match', 'pick', 'pick_label', 'actual',
        'odds_h', 'odds_d', 'odds_a', 'odds_picked',
        'correct', 'confidence',
        'home_form5', 'away_form5', 'form_gap',
        'home_draw_rate', 'away_draw_rate',
        'pin_prob_h', 'pin_prob_d', 'pin_prob_a',
        'reason', 'week_won', 'combo_odds',
    ]).sort_values(['week', 'confidence'], ascending=[True, False]).reset_index(drop=True)
    out_df.to_csv(out_path, index=False)

    # Compute stats
    total  = len(results)
    wins   = sum(1 for r in results if r['all_correct'])
    avg_o  = np.mean([r['combo_odds'] for r in results]) if results else 0
    ev     = (wins / total) * avg_o if total else 0

    pick_stats = {}
    for pick in ['H', 'D', 'A']:
        sub = out_df[out_df['pick'] == pick]
        if len(sub) == 0:
            pick_stats[pick] = {'n': 0, 'accuracy': None, 'avg_odds': None, 'ev': None}
        else:
            acc = sub['correct'].mean()
            ao  = sub['odds_picked'].mean()
            pick_stats[pick] = {'n': len(sub), 'accuracy': acc, 'avg_odds': ao, 'ev': acc * ao}

    season_stats = []
    for y in range(2021, 2026):
        sr = [r for r in results if str(y) in r['week'] or str(y+1) in r['week']]
        if not sr: continue
        sw = sum(1 for r in sr if r['all_correct'])
        ao = np.mean([r['combo_odds'] for r in sr])
        season_stats.append({
            'season': f"{y}/{y+1}", 'wins': sw, 'total': len(sr),
            'win_pct': sw/len(sr), 'avg_odds': ao, 'ev': sw/len(sr)*ao
        })

    summary = {
        'version': version, 'total_weeks': total, 'wins': wins,
        'win_pct': wins/total if total else 0, 'avg_odds': avg_o, 'ev': ev,
        'pick_stats': pick_stats, 'season_stats': season_stats,
        'results': results, 'csv_path': str(out_path),
    }

    _log_version(version, summary)
    return summary


def _log_version(version: str, s: dict):
    log_path = REPORT_DIR / "VERSIONS.md"
    existing = log_path.read_text() if log_path.exists() else "# Model Version Log\n\n"

    block = f"""
## {version} — {datetime.now().strftime('%Y-%m-%d %H:%M')}
- **Win rate:** {s['win_pct']:.1%}  |  **Avg odds:** {s['avg_odds']:.2f}x  |  **EV:** {s['ev']:.3f}
- **Weeks tested:** {s['total_weeks']}  |  **Wins:** {s['wins']}
- **Pick breakdown:**
"""
    for pick, ps in s['pick_stats'].items():
        if ps['n'] == 0: continue
        block += f"  - {pick}: {ps['n']} bets | {ps['accuracy']:.1%} acc | {ps['avg_odds']:.2f} avg odds | EV {ps['ev']:.3f}\n"
    block += "- **By season:**\n"
    for ss in s['season_stats']:
        block += f"  - {ss['season']}: {ss['wins']}/{ss['total']} = {ss['win_pct']:.0%}  EV={ss['ev']:.3f}\n"
    block += f"- CSV: `{Path(s['csv_path']).name}`\n"

    # Replace existing block for this version or append
    if f"## {version} " in existing:
        import re
        existing = re.sub(
            rf"## {version} .*?(?=\n## |\Z)", block.strip(), existing, flags=re.DOTALL)
    else:
        existing = existing.rstrip() + "\n" + block

    log_path.write_text(existing)


if __name__ == "__main__":
    for v in ['v1', 'v2', 'v3', 'v4', 'v5']:
        print(f"\n{'='*55}\nRunning {v}...\n{'='*55}")
        s = run_backtest(v)
        print(f"Win rate: {s['win_pct']:.1%} | Avg odds: {s['avg_odds']:.2f}x | EV: {s['ev']:.3f}")
        print("Pick breakdown:")
        for pick, ps in s['pick_stats'].items():
            if ps['n'] == 0: continue
            print(f"  {pick}: {ps['n']} bets | {ps['accuracy']:.1%} acc | {ps['avg_odds']:.2f} odds | EV {ps['ev']:.3f}")
        print("By season:")
        for ss in s['season_stats']:
            print(f"  {ss['season']}: {ss['wins']}/{ss['total']} = {ss['win_pct']:.0%}  EV={ss['ev']:.3f}")
