"""
Weekly recommendation emailer.
Run every Saturday morning via system cron:
  crontab -e
  0 8 * * 6 /Users/idoshveki/projects/winner-recommender/send_weekly.sh

Config: edit EMAIL_CONFIG below.
Gmail: use an App Password (myaccount.google.com/apppasswords).
"""

import sqlite3
import smtplib
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from recommend.combined_slip_backtest import (
    build_features, score_ha, score_corner, score_draw
)
from data.sportapi_form import get_fixtures_with_ids, get_league_form, LEAGUE_TO_TOURNEY

import pandas as pd

# ── CONFIG — edit these ───────────────────────────────────────────────────────
import os as _os
EMAIL_CONFIG = {
    "from_addr": "Winner Recommender <onboarding@resend.dev>",
    "api_key":   (_os.getenv("RESEND_API_KEY") or "").strip(),
    "to_addr":   ["idoshveki@gmail.com", "adicang@gmail.com", "tal@milgapo.co.il", "shvekiasaf@gmail.com"],
}
DB_PATH    = ROOT / "data" / "db" / "winner.db"
REPORT_DIR = ROOT / "data" / "reports"

SPORT_LEAGUE = {
    'soccer_epl': 'EPL',
    'soccer_spain_la_liga': 'La_Liga',
    'soccer_germany_bundesliga': 'Bundesliga',
    'soccer_italy_serie_a': 'Serie_A',
}
NAME_MAP = {
    'Brighton and Hove Albion': 'Brighton', 'Wolverhampton Wanderers': 'Wolves',
    'Atletico Madrid': 'Ath Madrid', 'Atlético Madrid': 'Ath Madrid',
    'Borussia Monchengladbach': "M'gladbach", 'AC Milan': 'Milan',
    'Inter Milan': 'Inter', 'AS Roma': 'Roma', 'Atalanta BC': 'Atalanta',
    'Bayer Leverkusen': 'Leverkusen', 'Eintracht Frankfurt': 'Ein Frankfurt',
    'Tottenham Hotspur': 'Tottenham', 'Manchester City': 'Man City',
    'Manchester United': 'Man United', 'Newcastle United': 'Newcastle',
    'Nottingham Forest': "Nott'm Forest", 'Leeds United': 'Leeds',
    'Athletic Bilbao': 'Ath Bilbao', 'CA Osasuna': 'Osasuna',
    'Alavés': 'Alaves', 'Rayo Vallecano': 'Vallecano',
    'Borussia Dortmund': 'Dortmund', 'FSV Mainz 05': 'Mainz',
    'TSG Hoffenheim': 'Hoffenheim', 'FC St. Pauli': 'St Pauli',
    'VfL Wolfsburg': 'Wolfsburg', 'VfB Stuttgart': 'Stuttgart',
    'FC Union Berlin': 'Union Berlin', 'SC Freiburg': 'Freiburg',
    'Hamburger SV': 'Hamburg', '1. FC Heidenheim': 'Heidenheim',
    'FC Augsburg': 'Augsburg', 'Hellas Verona': 'Verona',
    'Elche CF': 'Elche', 'Real Sociedad': 'Sociedad',
    'Deportivo Alaves': 'Alaves', 'Celta Vigo': 'Celta',
    'Real Betis': 'Betis', 'Espanyol': 'Espanol',
    'West Ham United': 'West Ham', 'Crystal Palace': 'Crystal Palace',
    # La Liga — SportAPI variants missing from original map
    'Levante UD': 'Levante', 'Real Oviedo': 'Oviedo', 'Girona FC': 'Girona',
    'Deportivo Alavés': 'Alaves', 'Athletic Club': 'Ath Bilbao',
    # Bundesliga — SportAPI variants missing from original map
    'FC Bayern München': 'Bayern Munich', '1. FC Union Berlin': 'Union Berlin',
    '1. FSV Mainz 05': 'Mainz', 'Bayer 04 Leverkusen': 'Leverkusen',
    "Borussia M'gladbach": "M'gladbach", 'SV Werder Bremen': 'Werder Bremen',
    '1. FC Köln': 'FC Koln',
    # EPL — SportAPI variants missing from original map
    'Brighton & Hove Albion': 'Brighton',
}

UNRELIABLE_HOME = {'Tottenham', 'Man United', 'Chelsea', 'Brighton',
                   'West Ham', 'Bournemouth'}


def get_yc_avgs(history_df):
    """Build per-team rolling yellow card averages from history (venue-specific)."""
    team_home_yc = {}
    team_away_yc = {}
    for _, row in history_df.iterrows():
        ht, at = row['home_team'], row['away_team']
        hyc = row.get('home_yellow')
        ayc = row.get('away_yellow')
        if pd.notna(hyc):
            team_home_yc.setdefault(ht, []).append(float(hyc))
        if pd.notna(ayc):
            team_away_yc.setdefault(at, []).append(float(ayc))
    return team_home_yc, team_away_yc


def get_form(history_df, team, venue, n=5):
    """Get last-n venue pts, trend, draw rate, goals for a team."""
    if venue == 'home':
        games = history_df[history_df['home_team'] == team].tail(n * 2)
        pts = sum(3 if r == 'H' else (1 if r == 'D' else 0)
                  for r in games['result'].tail(n))
        recent3 = sum(3 if r == 'H' else (1 if r == 'D' else 0)
                      for r in games['result'].tail(3))
        prior3  = sum(3 if r == 'H' else (1 if r == 'D' else 0)
                      for r in games['result'].tail(6).head(3))
        dr10    = (games['result'].tail(10) == 'D').mean()
        gf5     = games['home_goals'].tail(n).mean()
        ga5     = games['away_goals'].tail(n).mean()
        streak_col = games['result'].tail(10).tolist()
    else:
        games = history_df[history_df['away_team'] == team].tail(n * 2)
        pts = sum(3 if r == 'A' else (1 if r == 'D' else 0)
                  for r in games['result'].tail(n))
        recent3 = sum(3 if r == 'A' else (1 if r == 'D' else 0)
                      for r in games['result'].tail(3))
        prior3  = sum(3 if r == 'A' else (1 if r == 'D' else 0)
                      for r in games['result'].tail(6).head(3))
        dr10    = (games['result'].tail(10) == 'D').mean()
        gf5     = games['away_goals'].tail(n).mean()
        ga5     = games['home_goals'].tail(n).mean()
        streak_col = games['result'].tail(10).tolist()

    win_char = 'H' if venue == 'home' else 'A'
    lose_char = 'A' if venue == 'home' else 'H'
    streak = 0
    for r in reversed(streak_col):
        if r == win_char: streak += 1
        else: break
    lstreak = 0
    for r in reversed(streak_col):
        if r == lose_char: lstreak += 1
        else: break

    return {
        'pts': pts, 'trend': recent3 - prior3, 'dr10': dr10,
        'gf5': round(gf5, 1) if not pd.isna(gf5) else 0,
        'ga5': round(ga5, 1) if not pd.isna(ga5) else 0,
        'streak': streak, 'lstreak': lstreak,
    }


def generate_picks():
    conn = sqlite3.connect(DB_PATH)

    history = pd.read_sql("""
        SELECT league, date, home_team, away_team,
               home_goals, away_goals, result,
               home_corners, away_corners,
               home_yellow, away_yellow
        FROM matches_history
        WHERE result IS NOT NULL
        ORDER BY date
    """, conn)

    pinnacle_odds = pd.read_sql("""
        SELECT home_team, away_team, outcome_name, price
        FROM odds_raw
        WHERE bookmaker = 'pinnacle' AND market = 'h2h'
          AND commence_time <= datetime('now', '+5 days')
          AND commence_time >= datetime('now', '-1 hours')
    """, conn)

    conn.close()

    history['date'] = pd.to_datetime(history['date'])
    history = history.sort_values('date').reset_index(drop=True)

    # Build yellow card averages from history (venue-specific rolling avgs)
    team_home_yc, team_away_yc = get_yc_avgs(history)

    # ── Fetch upcoming fixtures + team IDs from SportAPI ──────────────────────
    print("Fetching upcoming fixtures from SportAPI...")
    sportapi_fixtures = get_fixtures_with_ids(days=5)
    print(f"  {len(sportapi_fixtures)} fixtures found across 4 leagues")

    pinnacle_odds['home_team'] = pinnacle_odds['home_team'].map(lambda x: NAME_MAP.get(x, x))
    pinnacle_odds['away_team'] = pinnacle_odds['away_team'].map(lambda x: NAME_MAP.get(x, x))

    ha_picks, yc_picks, btts_picks, draw_picks = [], [], [], []

    for fix in sportapi_fixtures:
        ht_raw, at_raw = fix['home'], fix['away']
        ht = NAME_MAP.get(ht_raw, ht_raw)
        at = NAME_MAP.get(at_raw, at_raw)
        league        = fix['league']
        kickoff       = fix['date']
        kickoff_ts    = fix['kickoff_ts']
        tournament_id = fix['tournament_id']
        event_id      = fix['event_id']

        # ── Real-time form from SportAPI (needed by all scorers) ──────────
        print(f"  Form: {ht} vs {at} ({league})...")
        hf = get_league_form(fix['home_id'], tournament_id, 'home')
        af = get_league_form(fix['away_id'], tournament_id, 'away')
        venue_gap = hf['pts'] - af['pts']
        pts5_diff = hf['pts'] - af['pts']

        # ── YC Over 3.5 scorer (all leagues, no Pinnacle odds needed) ─────
        h_yc_hist = team_home_yc.get(ht, [])[-5:]
        a_yc_hist = team_away_yc.get(at, [])[-5:]
        h_yc5 = sum(h_yc_hist) / max(len(h_yc_hist), 1)
        a_yc5 = sum(a_yc_hist) / max(len(a_yc_hist), 1)
        yc_pred = h_yc5 + a_yc5
        if yc_pred >= 3.5 and len(h_yc_hist) >= 3 and len(a_yc_hist) >= 3:
            yc_odds = 1.60 if league == 'Bundesliga' else 1.50
            yc_picks.append({
                'market': 'YC Over 3.5',
                'match': f"{ht} vs {at}", 'league': league, 'kickoff': kickoff,
                'kickoff_ts': kickoff_ts, 'event_id': event_id,
                'pick': 'Over 3.5', 'odds': yc_odds, 'conf': round(yc_pred, 2),
                'why': f"yc_pred={yc_pred:.1f} (home_avg={h_yc5:.1f} + away_avg={a_yc5:.1f})",
            })

        # ── Over 2.5 + BTTS scorer (no Pinnacle odds needed) ──────────────
        if hf['gf5'] >= 1.8 and af['gf5'] >= 1.5:
            btts_picks.append({
                'market': 'O2.5+BTTS',
                'match': f"{ht} vs {at}", 'league': league, 'kickoff': kickoff,
                'kickoff_ts': kickoff_ts, 'event_id': event_id,
                'pick': 'Over 2.5 And Yes', 'odds': 2.63,
                'conf': round(hf['gf5'] + af['gf5'], 2),
                'why': f"home_gf5={hf['gf5']} away_gf5={af['gf5']}",
            })

        # ── H/A + Draw scorers need Pinnacle odds — skip if not available ──
        p = pinnacle_odds[(pinnacle_odds['home_team'] == ht) &
                          (pinnacle_odds['away_team'] == at)]
        ph_odds = p[p['outcome_name'] == ht]['price'].values
        pa_odds = p[p['outcome_name'] == at]['price'].values
        pd_odds = p[p['outcome_name'] == 'Draw']['price'].values

        if len(ph_odds) == 0:
            continue

        ph_o = float(ph_odds[0])
        pa_o = float(pa_odds[0]) if len(pa_odds) else 0
        pd_o = float(pd_odds[0]) if len(pd_odds) else 0

        # Vig-remove
        vig = 1/ph_o + (1/pa_o if pa_o else 0) + (1/pd_o if pd_o else 0)
        ph = (1/ph_o) / vig
        pa = (1/pa_o) / vig if pa_o else 0
        pd_ = (1/pd_o) / vig if pd_o else 0

        # ── H/A scorer ─────────────────────────────────────────────────────
        if ph >= 0.63 and venue_gap >= 5 and pts5_diff >= 5 and hf['trend'] >= 0:
            if ht not in UNRELIABLE_HOME or ph >= 0.72:
                conf = ph * 10
                if hf['streak'] >= 3: conf *= 1.25
                elif hf['streak'] >= 2: conf *= 1.10
                if af['lstreak'] >= 2: conf *= 1.15
                if hf['gf5'] > 1.8 and hf['ga5'] < 1.5: conf *= 1.20
                if hf['pts'] >= 12 and af['pts'] <= 3: conf *= 1.50
                ha_picks.append({
                    'match': f"{ht} vs {at}", 'league': league, 'kickoff': kickoff,
                    'pick': 'H', 'odds': ph_o, 'conf': round(conf, 1),
                    'why': f"ph={ph:.0%} venue_gap={venue_gap} streak={hf['streak']}",
                })

        elif pa >= 0.58 and venue_gap <= -5 and pts5_diff <= -5 and af['trend'] >= 0:
            conf = pa * 10
            if af['streak'] >= 3: conf *= 1.25
            elif af['streak'] >= 2: conf *= 1.10
            if hf['lstreak'] >= 2: conf *= 1.15
            if af['gf5'] > 1.8 and af['ga5'] < 1.5: conf *= 1.20
            ha_picks.append({
                'match': f"{ht} vs {at}", 'league': league, 'kickoff': kickoff,
                'pick': 'A', 'odds': pa_o, 'conf': round(conf, 1),
                'why': f"pa={pa:.0%} venue_gap={venue_gap}",
            })

        # ── Draw scorer ────────────────────────────────────────────────────
        if pd_ >= 0.29 and abs(pts5_diff) <= 1 and hf['dr10'] > 0.20 and af['dr10'] > 0.20:
            draw_picks.append({
                'match': f"{ht} vs {at}", 'league': league, 'kickoff': kickoff,
                'pick': 'D', 'odds': pd_o, 'conf': round(pd_ * 100, 1),
                'why': f"pd={pd_:.0%} gap={pts5_diff} home_dr={hf['dr10']:.0%} away_dr={af['dr10']:.0%}",
            })

    # ── Select best picks — decision tree ─────────────────────────────────
    ha_picks.sort(key=lambda x: -x['conf'])
    yc_picks.sort(key=lambda x: -x['conf'])
    btts_picks.sort(key=lambda x: -x['conf'])
    draw_picks.sort(key=lambda x: -x['conf'])

    best_ha = ha_picks[0] if ha_picks else None
    ha_match = best_ha['match'] if best_ha else None

    # Priority: YC Over 4.5 (La Liga) > BTTS Yes > 2nd H/A (different match)
    best_leg2 = None

    # 1. YC Over 4.5 — must be from a different match than H/A
    for yc in yc_picks:
        if yc['match'] != ha_match:
            best_leg2 = yc
            break

    # 2. BTTS Yes fallback
    if best_leg2 is None:
        for btts in btts_picks:
            if btts['match'] != ha_match:
                best_leg2 = btts
                break

    # 3. 2nd H/A pick fallback (from ha_picks[1:])
    if best_leg2 is None and len(ha_picks) > 1:
        for ha in ha_picks[1:]:
            if ha['match'] != ha_match:
                leg2 = dict(ha)
                leg2['market'] = 'H/A'
                leg2['pick'] = f"{'Home' if ha['pick'] == 'H' else 'Away'}"
                best_leg2 = leg2
                break

    best_draw = draw_picks[0] if draw_picks else None

    return best_ha, best_leg2, best_draw, ha_picks, draw_picks, yc_picks, btts_picks


def format_email(best_ha, best_leg2, best_draw, all_ha, all_draws):
    today = datetime.today().strftime('%Y-%m-%d')
    lines = [f"Winner Picks — {today}", "=" * 40, ""]

    # Slip 1
    lines.append("SLIP 1 — COMBINED BET")
    lines.append("-" * 30)
    if best_ha:
        combined_odds = round(best_ha['odds'] * (best_leg2['odds'] if best_leg2 else 1), 2)
        lines.append(f"Leg 1 (H/A):  {best_ha['match']} → {best_ha['pick']} @ {best_ha['odds']:.2f}")
        lines.append(f"  Why: {best_ha['why']}")
        if best_leg2:
            mkt = best_leg2.get('market', '')
            lines.append(f"Leg 2 ({mkt}): {best_leg2['match']} → {best_leg2['pick']} @ {best_leg2['odds']:.2f}")
            lines.append(f"  Why: {best_leg2['why']}")
            lines.append(f"Combined odds: {combined_odds:.2f}x")
        else:
            lines.append("Leg 2: No qualifying second leg this week")
            lines.append(f"Combined odds: {best_ha['odds']:.2f}x (single leg)")
    else:
        lines.append("No qualifying H/A pick this week — SKIP SLIP 1")

    lines.append("")
    lines.append("SLIP 2 — DRAW SINGLE")
    lines.append("-" * 30)
    if best_draw:
        lines.append(f"{best_draw['match']} → Draw @ {best_draw['odds']:.2f}")
        lines.append(f"  Why: {best_draw['why']}")
    else:
        lines.append("No qualifying draw pick this week — SKIP SLIP 2")

    if len(all_ha) > 1:
        lines.append("")
        lines.append("OTHER H/A CANDIDATES (not used):")
        for p in all_ha[1:4]:
            lines.append(f"  {p['match']} → {p['pick']} @ {p['odds']:.2f} (conf={p['conf']})")

    if len(all_draws) > 1:
        lines.append("")
        lines.append("OTHER DRAW CANDIDATES:")
        for p in all_draws[1:3]:
            lines.append(f"  {p['match']} → Draw @ {p['odds']:.2f} (conf={p['conf']})")

    lines.append("")
    lines.append("Model: EV 1.68 | 75% slip win rate (last 27 weeks)")
    lines.append("Good luck!")

    return "\n".join(lines)


def save_picks_to_db(best_ha, best_leg2, best_draw):
    """Persist this week's live picks to weekly_picks table. INSERT OR IGNORE so re-runs are safe."""
    from datetime import timedelta
    today = datetime.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end   = week_start + timedelta(days=6)           # Sunday
    week_label = f"{week_start.strftime('%Y-%m-%d')}/{week_end.strftime('%Y-%m-%d')}"

    n_legs = 0
    combined_odds = None
    if best_ha:
        n_legs = 1
        combined_odds = best_ha['odds']
        if best_leg2:
            n_legs = 2
            combined_odds = round(best_ha['odds'] * best_leg2['odds'], 2)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_picks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            week          TEXT UNIQUE NOT NULL,
            generated_at  TEXT NOT NULL,
            n_legs        INTEGER,
            combined_odds REAL,
            slip_won      INTEGER,
            leg1_market   TEXT, leg1_match TEXT, leg1_pick TEXT,
            leg1_odds     REAL, leg1_why   TEXT, leg1_hit  INTEGER,
            leg2_market   TEXT, leg2_match TEXT, leg2_pick TEXT,
            leg2_odds     REAL, leg2_why   TEXT, leg2_hit  INTEGER,
            draw_match    TEXT, draw_pick  TEXT,
            draw_odds     REAL, draw_hit   INTEGER
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO weekly_picks
            (week, generated_at, n_legs, combined_odds, slip_won,
             leg1_market, leg1_match, leg1_pick, leg1_odds, leg1_why, leg1_hit,
             leg2_market, leg2_match, leg2_pick, leg2_odds, leg2_why, leg2_hit,
             draw_match, draw_pick, draw_odds, draw_hit)
        VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL)
    """, (
        week_label, datetime.now().isoformat(), n_legs, combined_odds,
        'H/A',
        best_ha['match'] if best_ha else None,
        best_ha['pick']  if best_ha else None,
        best_ha['odds']  if best_ha else None,
        best_ha['why']   if best_ha else None,
        best_leg2.get('market', 'H/A') if best_leg2 else None,
        best_leg2['match'] if best_leg2 else None,
        best_leg2['pick']  if best_leg2 else None,
        best_leg2['odds']  if best_leg2 else None,
        best_leg2['why']   if best_leg2 else None,
        best_draw['match'] if best_draw else None,
        'D'                if best_draw else None,
        best_draw['odds']  if best_draw else None,
    ))
    conn.commit()
    conn.close()
    print(f"Picks saved to DB — week {week_label}")


def send_email(subject, body):
    import resend
    cfg = EMAIL_CONFIG
    to_list = cfg['to_addr'] if isinstance(cfg['to_addr'], list) else [cfg['to_addr']]
    resend.api_key = cfg['api_key']
    resend.Emails.send({
        "from":    cfg['from_addr'],
        "to":      to_list,
        "subject": subject,
        "text":    body,
    })
    print(f"Email sent to {', '.join(to_list)}")


if __name__ == "__main__":
    import subprocess, os

    # 1. Refresh odds
    import sys as _sys
    script_dir = Path(__file__).resolve().parents[2]
    fetch_script = script_dir / "src" / "data" / "fetch_odds.py"
    print("Fetching latest odds...")
    subprocess.run([_sys.executable, str(fetch_script)], cwd=str(script_dir))

    # 2. Generate picks
    print("Generating picks...")
    best_ha, best_leg2, best_draw, all_ha, all_draws, _yc, _btts = generate_picks()

    # 3. Save picks to DB (permanent record)
    save_picks_to_db(best_ha, best_leg2, best_draw)

    # 4. Format & send
    body = format_email(best_ha, best_leg2, best_draw, all_ha, all_draws)
    print("\n" + body)

    today = datetime.today().strftime('%Y-%m-%d')
    send_email(f"Winner Picks {today}", body)
