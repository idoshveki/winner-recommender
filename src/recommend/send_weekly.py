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
EMAIL_CONFIG = {
    "smtp_host":  "smtp.gmail.com",
    "smtp_port":  587,
    "from_addr":  "idoshveki@gmail.com",
    "app_password": "YOUR_APP_PASSWORD",        # ← fill in from myaccount.google.com/apppasswords
    "to_addr":    "idoshveki@gmail.com",
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
}

UNRELIABLE_HOME = {'Tottenham', 'Man United', 'Chelsea', 'Brighton',
                   'West Ham', 'Bournemouth'}


def get_corner_avgs(history_df):
    """Build per-team rolling corner averages from history."""
    team_home = {}
    team_away = {}
    for _, row in history_df.iterrows():
        ht, at = row['home_team'], row['away_team']
        hc = row.get('home_corners')
        ac = row.get('away_corners')
        if pd.notna(hc) and pd.notna(ac):
            team_home.setdefault(ht, []).append(float(hc))
            team_away.setdefault(at, []).append(float(ac))
    return team_home, team_away


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
               home_corners, away_corners
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

    # Build corner averages from history (SportAPI doesn't provide corners)
    team_home_crn, team_away_crn = get_corner_avgs(history)

    # ── Fetch upcoming fixtures + team IDs from SportAPI ──────────────────────
    print("Fetching upcoming fixtures from SportAPI...")
    sportapi_fixtures = get_fixtures_with_ids(days=5)
    print(f"  {len(sportapi_fixtures)} fixtures found across 4 leagues")

    pinnacle_odds['home_team'] = pinnacle_odds['home_team'].map(lambda x: NAME_MAP.get(x, x))
    pinnacle_odds['away_team'] = pinnacle_odds['away_team'].map(lambda x: NAME_MAP.get(x, x))

    ha_picks, corner_picks, draw_picks = [], [], []

    for fix in sportapi_fixtures:
        ht_raw, at_raw = fix['home'], fix['away']
        ht = NAME_MAP.get(ht_raw, ht_raw)
        at = NAME_MAP.get(at_raw, at_raw)
        league = fix['league']
        kickoff = fix['date']
        tournament_id = fix['tournament_id']

        # Get Pinnacle odds — match by our internal names
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

        # ── Real-time form from SportAPI ──────────────────────────────────────
        print(f"  Form: {ht} vs {at} ({league})...")
        hf = get_league_form(fix['home_id'], tournament_id, 'home')
        af = get_league_form(fix['away_id'], tournament_id, 'away')
        venue_gap = hf['pts'] - af['pts']
        pts5_diff = hf['pts'] - af['pts']

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

        # ── Corner scorer ──────────────────────────────────────────────────
        h_cf = sum(team_home_crn.get(ht, [0])[-5:]) / max(len(team_home_crn.get(ht, [0])[-5:]), 1)
        a_cf = sum(team_away_crn.get(at, [0])[-5:]) / max(len(team_away_crn.get(at, [0])[-5:]), 1)
        total = h_cf + a_cf
        exp_h = h_cf / max(total, 0.1)
        if exp_h > 0.65:
            corner_picks.append({
                'match': f"{ht} vs {at}", 'league': league, 'kickoff': kickoff,
                'pick': 'H', 'odds': 1.80, 'conf': round((exp_h - 0.50) * 20, 1),
                'why': f"home generates {exp_h:.0%} of corners (h_avg={h_cf:.1f} vs a_avg={a_cf:.1f})",
            })
        elif exp_h < 0.35:
            corner_picks.append({
                'match': f"{ht} vs {at}", 'league': league, 'kickoff': kickoff,
                'pick': 'A', 'odds': 1.80, 'conf': round((0.50 - exp_h) * 20, 1),
                'why': f"away generates {1-exp_h:.0%} of corners (h_avg={h_cf:.1f} vs a_avg={a_cf:.1f})",
            })

    # ── Select best picks ──────────────────────────────────────────────────
    ha_picks.sort(key=lambda x: -x['conf'])
    draw_picks.sort(key=lambda x: -x['conf'])
    corner_picks.sort(key=lambda x: -x['conf'])

    best_ha = ha_picks[0] if ha_picks else None

    # Best corner from a different match than H/A pick
    best_crn = None
    for c in corner_picks:
        if best_ha is None or c['match'] != best_ha['match']:
            best_crn = c
            break

    best_draw = draw_picks[0] if draw_picks else None

    return best_ha, best_crn, best_draw, ha_picks, draw_picks


def format_email(best_ha, best_crn, best_draw, all_ha, all_draws):
    today = datetime.today().strftime('%Y-%m-%d')
    lines = [f"Winner Picks — {today}", "=" * 40, ""]

    # Slip 1
    lines.append("SLIP 1 — COMBINED BET")
    lines.append("-" * 30)
    if best_ha:
        combined_odds = round(best_ha['odds'] * (best_crn['odds'] if best_crn else 1), 2)
        lines.append(f"Leg 1 (H/A):    {best_ha['match']} → {best_ha['pick']} @ {best_ha['odds']:.2f}")
        lines.append(f"  Why: {best_ha['why']}")
        if best_crn:
            lines.append(f"Leg 2 (Corner): {best_crn['match']} → 1st corner {best_crn['pick']} @ {best_crn['odds']:.2f}")
            lines.append(f"  Why: {best_crn['why']}")
            lines.append(f"Combined odds:  {combined_odds:.2f}x")
        else:
            lines.append("Leg 2 (Corner): No qualifying corner pick this week")
            lines.append(f"Combined odds:  {best_ha['odds']:.2f}x (single leg)")
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


def send_email(subject, body):
    cfg = EMAIL_CONFIG
    msg = MIMEMultipart()
    msg['From']    = cfg['from_addr']
    msg['To']      = cfg['to_addr']
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port']) as server:
        server.starttls()
        server.login(cfg['from_addr'], cfg['app_password'])
        server.send_message(msg)
    print(f"Email sent to {cfg['to_addr']}")


if __name__ == "__main__":
    import subprocess, os

    # 1. Refresh odds
    script_dir = Path(__file__).resolve().parents[2]
    venv_python = script_dir / ".venv" / "bin" / "python"
    fetch_script = script_dir / "src" / "data" / "fetch_odds.py"
    print("Fetching latest odds...")
    subprocess.run([str(venv_python), str(fetch_script)], cwd=str(script_dir))

    # 2. Generate picks
    print("Generating picks...")
    best_ha, best_crn, best_draw, all_ha, all_draws = generate_picks()

    # 3. Format & send
    body = format_email(best_ha, best_crn, best_draw, all_ha, all_draws)
    print("\n" + body)

    today = datetime.today().strftime('%Y-%m-%d')
    send_email(f"Winner Picks {today}", body)
