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
from data.fetch_oddsportal import fetch_live_odds_for_picks

import pandas as pd

# ── CONFIG — edit these ───────────────────────────────────────────────────────
import os as _os
EMAIL_CONFIG = {
    "smtp_host": _os.getenv("SMTP_HOST", "smtp.gmail.com"),
    "smtp_port": int(_os.getenv("SMTP_PORT", "587")),
    "from_addr": _os.getenv("SMTP_FROM", ""),        # e.g. yourname@gmail.com
    "password":  _os.getenv("SMTP_PASSWORD", ""),    # Gmail: use App Password
    "to_addr":   [a.strip() for a in _os.getenv("SMTP_TO", "idoshveki@gmail.com").split(",")],
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

# ── Phase 3: EV helpers ───────────────────────────────────────────────────────
BTTS_MIN_EV = 0.02   # minimum EV to include a BTTS pick (allow slightly below 5% to not over-filter)
YC_MIN_EV   = 0.00   # YC: already gated by yc_pred >= 6.0 which calibrates to EV +6.7%

# Calibrated BTTS+O2.5 hit rates (home_gf5 + away_gf5 thresholds, from Phase 1)
# Used to compute EV from live odds
def btts_est_prob(hgf5, agf5):
    """Estimate BTTS+O2.5 probability from form stats (calibrated in Phase 1)."""
    total = hgf5 + agf5
    # Base 50.8% at threshold (3.6), scaling up with goal output
    return min(0.65, 0.508 + max(0, total - 3.6) * 0.025)

def yc_est_prob(yc_pred):
    """Estimate YC Over 3.5 probability from yc_pred (calibrated hit rates)."""
    if yc_pred >= 6.0: return 0.728
    if yc_pred >= 5.5: return 0.670
    if yc_pred >= 5.0: return 0.658
    if yc_pred >= 4.5: return 0.667
    return 0.600

def estimate_1win_btts_o25(ref_btts, ref_ou25):
    """
    Estimate 1win's BTTS+O2.5 combined odds from bet365 reference odds.
    - Multiply component probabilities (P_btts * P_ou25), apply correlation discount (~0.88)
      since BTTS and O2.5 are positively correlated (both teams scoring helps reach O2.5)
    - Apply 1win markup (~+8% better odds than bet365)
    """
    p_btts = 1 / ref_btts
    p_ou25 = 1 / ref_ou25
    # Correlation-adjusted combined probability
    p_combined = p_btts * p_ou25 / 0.88
    # Fair odds, then 1win markup (1win ~5-8% better than bet365)
    fair_odds = 1 / p_combined
    return round(fair_odds * 1.06, 2)


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
          AND commence_time <= datetime('now', '+7 days')
          AND commence_time >= datetime('now', '-1 hours')
    """, conn)

    conn.close()

    history['date'] = pd.to_datetime(history['date'])
    history = history.sort_values('date').reset_index(drop=True)

    # Build yellow card averages from history (venue-specific rolling avgs)
    team_home_yc, team_away_yc = get_yc_avgs(history)

    # ── Fetch upcoming fixtures + team IDs from SportAPI ──────────────────────
    print("Fetching upcoming fixtures from SportAPI...")
    sportapi_fixtures = get_fixtures_with_ids(days=7)
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

        # ── YC Over 3.5 scorer ────────────────────────────────────────────
        # Threshold: yc_pred >= 6.0 (backtested: 71% hit rate, EV +0.067 @ 1.50)
        # Below 6.0: hit rate drops to 63-67%, EV negative at 1.50 odds
        h_yc_hist = team_home_yc.get(ht, [])[-5:]
        a_yc_hist = team_away_yc.get(at, [])[-5:]
        h_yc5 = sum(h_yc_hist) / max(len(h_yc_hist), 1)
        a_yc5 = sum(a_yc_hist) / max(len(a_yc_hist), 1)
        yc_pred = h_yc5 + a_yc5
        if yc_pred >= 6.0 and len(h_yc_hist) >= 3 and len(a_yc_hist) >= 3:
            yc_odds = 1.60 if league == 'Bundesliga' else 1.50
            yc_picks.append({
                'market': 'YC Over 3.5',
                'match': f"{ht} vs {at}", 'league': league, 'kickoff': kickoff,
                'kickoff_ts': kickoff_ts, 'event_id': event_id,
                'pick': 'Over 3.5', 'odds': yc_odds, 'conf': round(yc_pred, 2),
                'why': f"yc_pred={yc_pred:.1f} (home_avg={h_yc5:.1f} + away_avg={a_yc5:.1f})",
            })

        # ── Over 2.5 + BTTS scorer ────────────────────────────────────────
        # Threshold: home_gf5 >= 1.8 AND away_gf5 >= 1.8 (backtested: 50.8% hit rate, EV +0.068 @ 2.10)
        if hf['gf5'] >= 1.8 and af['gf5'] >= 1.8:
            btts_picks.append({
                'market': 'O2.5+BTTS',
                'match': f"{ht} vs {at}", 'league': league, 'kickoff': kickoff,
                'kickoff_ts': kickoff_ts, 'event_id': event_id,
                'pick': 'Over 2.5 And Yes', 'odds': 2.10,
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

    # ── Phase 3: enrich BTTS picks with live reference odds from OddsPortal ──
    print("Fetching live reference odds from OddsPortal (Phase 3)...")
    try:
        live_ref = fetch_live_odds_for_picks(btts_picks)
        for pick in btts_picks:
            ref = live_ref.get(pick['match'])
            if ref:
                est_odds = estimate_1win_btts_o25(ref['btts'], ref['ou25'])
                pick['odds'] = est_odds
                pick['ref_note'] = f"bet365 BTTS={ref['btts']:.2f} O/U={ref['ou25']:.2f} → est={est_odds:.2f}"
            # Compute EV for every BTTS pick (whether live odds found or not)
            m = __import__('re').search(r'home_gf5=([\d.]+).*away_gf5=([\d.]+)', pick.get('why',''))
            hgf5 = float(m.group(1)) if m else 1.8
            agf5 = float(m.group(2)) if m else 1.8
            pick['ev'] = round(btts_est_prob(hgf5, agf5) * pick['odds'] - 1, 3)
    except Exception as e:
        print(f"  Live odds fetch failed: {e} — using assumed odds")

    # Add EV to YC picks (using calibrated hit rates, assumed odds)
    for pick in yc_picks:
        prob = yc_est_prob(pick.get('conf', 5.0))
        pick['ev'] = round(prob * pick['odds'] - 1, 3)

    # Filter BTTS by minimum EV
    btts_picks = [p for p in btts_picks if p.get('ev', 0) >= BTTS_MIN_EV]

    # ── Select best picks — decision tree ─────────────────────────────────
    ha_picks.sort(key=lambda x: -x['conf'])
    yc_picks.sort(key=lambda x: -x['conf'])
    btts_picks.sort(key=lambda x: -x['conf'])
    draw_picks.sort(key=lambda x: -x['conf'])

    best_ha = ha_picks[0] if ha_picks else None
    ha_match = best_ha['match'] if best_ha else None

    # YC and BTTS picks from different matches than the H/A leg
    yc_other   = [p for p in yc_picks   if p['match'] != ha_match]
    btts_other = [p for p in btts_picks if p['match'] != ha_match]

    # ── Slip structure priority (backtested) ──────────────────────────────
    # 1. HA + YC + YC  — best EV (1.370), needs 2 YC picks at yc_pred >= 6
    # 2. HA + YC       — EV 1.184, needs 1 YC pick
    # 3. HA + BTTS     — EV 1.045, fallback when no YC qualifies
    # 4. HA only       — last resort

    best_leg2 = None
    best_leg3 = None  # third leg for HA+YC+YC

    if len(yc_other) >= 2:
        # HA + YC + YC: use top 2 YC picks (different matches from each other and HA)
        best_leg2 = yc_other[0]
        # leg3 must also differ from leg2
        for yc in yc_other[1:]:
            if yc['match'] != best_leg2['match']:
                best_leg3 = yc
                break
        if best_leg3 is None:
            best_leg3 = None  # fall back to 2-leg slip
    elif len(yc_other) == 1:
        best_leg2 = yc_other[0]
    elif btts_other:
        best_leg2 = btts_other[0]
    elif len(ha_picks) > 1:
        for ha in ha_picks[1:]:
            if ha['match'] != ha_match:
                leg2 = dict(ha)
                leg2['market'] = 'H/A'
                best_leg2 = leg2
                break

    best_draw = draw_picks[0] if draw_picks else None

    return best_ha, best_leg2, best_leg3, best_draw, ha_picks, draw_picks, yc_picks, btts_picks


def _td(val, bold=False):
    b = "font-weight:600;" if bold else ""
    return f'<td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;{b}">{val}</td>'


def _table(headers, rows, col_styles=None):
    """Render a simple HTML table. col_styles: list of extra CSS per column."""
    col_styles = col_styles or [""] * len(headers)
    ths = "".join(
        f'<th style="padding:8px 12px;text-align:left;background:#f3f4f6;'
        f'font-size:11px;text-transform:uppercase;color:#6b7280;'
        f'letter-spacing:.05em;border-bottom:2px solid #e5e7eb;{col_styles[i]}">'
        f'{h}</th>'
        for i, h in enumerate(headers)
    )
    trs = ""
    for row in rows:
        trs += "<tr>" + "".join(
            f'<td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;'
            f'font-size:13px;color:#111827;{col_styles[i]}">{v}</td>'
            for i, v in enumerate(row)
        ) + "</tr>"
    return (
        '<table style="width:100%;border-collapse:collapse;margin-bottom:24px">'
        f'<thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>'
    )


def format_email(best_ha, best_leg2, best_leg3, best_draw, all_ha, all_draws, all_yc=None, all_btts=None):
    today = datetime.today().strftime('%Y-%m-%d')
    all_yc = all_yc or []
    all_btts = all_btts or []

    # ── colour helpers ────────────────────────────────────────────────────────
    GREEN  = "#16a34a"
    AMBER  = "#d97706"
    GRAY   = "#6b7280"
    DARK   = "#111827"

    def badge(text, color):
        return (f'<span style="display:inline-block;padding:2px 8px;border-radius:12px;'
                f'background:{color}22;color:{color};font-size:11px;font-weight:700;'
                f'letter-spacing:.04em">{text}</span>')

    def odds_pill(val):
        return (f'<span style="font-weight:700;color:{DARK}">{val:.2f}x</span>')

    def section_title(text):
        return (f'<p style="margin:0 0 8px;font-size:11px;font-weight:700;'
                f'text-transform:uppercase;letter-spacing:.08em;color:{GRAY}">{text}</p>')

    def pick_card(label, match, pick, odds, why, color=GREEN):
        return (
            f'<div style="background:#f9fafb;border-left:4px solid {color};'
            f'border-radius:4px;padding:12px 16px;margin-bottom:10px">'
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="font-size:12px;color:{GRAY};font-weight:600">{label}</td>'
            f'<td align="right" style="font-size:16px;font-weight:800;color:{color}">{odds:.2f}x</td>'
            f'</tr></table>'
            f'<div style="font-size:15px;font-weight:700;color:{DARK};margin:6px 0 2px">'
            f'{match}</div>'
            f'<div style="font-size:13px;color:#374151">{pick}</div>'
            f'<div style="font-size:11px;color:{GRAY};margin-top:4px">{why}</div>'
            f'</div>'
        )

    # ── build body ────────────────────────────────────────────────────────────
    body = ""

    # Header
    body += (
        f'<div style="background:linear-gradient(135deg,#1e3a5f,#2563eb);'
        f'padding:28px 32px;border-radius:8px 8px 0 0">'
        f'<h1 style="margin:0;color:#fff;font-size:22px;font-weight:800;'
        f'letter-spacing:-.01em">🏆 Winner Picks</h1>'
        f'<p style="margin:4px 0 0;color:#93c5fd;font-size:13px">{today}</p>'
        f'</div>'
    )

    body += '<div style="padding:24px 32px;background:#fff">'

    # ── SLIP 1 ────────────────────────────────────────────────────────────────
    body += (f'<h2 style="margin:0 0 14px;font-size:15px;font-weight:800;'
             f'color:{DARK};border-bottom:2px solid #e5e7eb;padding-bottom:8px">'
             f'SLIP 1 — COMBINED BET</h2>')

    if best_ha:
        legs = [best_ha] + ([best_leg2] if best_leg2 else []) + ([best_leg3] if best_leg3 else [])
        combined_odds = round(float(pd.Series([l['odds'] for l in legs]).prod()), 2)
        body += pick_card("LEG 1 · H/A", best_ha['match'],
                          best_ha['pick'], best_ha['odds'], best_ha['why'])
        if best_leg2:
            mkt = best_leg2.get('market', 'H/A')
            body += pick_card(f"LEG 2 · {mkt}", best_leg2['match'],
                              best_leg2['pick'], best_leg2['odds'], best_leg2['why'], AMBER)
        if best_leg3:
            mkt3 = best_leg3.get('market', 'YC Over 3.5')
            body += pick_card(f"LEG 3 · {mkt3}", best_leg3['match'],
                              best_leg3['pick'], best_leg3['odds'], best_leg3['why'], "#ea580c")
        body += (f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#1e3a5f;'
                 f'border-radius:6px;margin-bottom:20px"><tr>'
                 f'<td style="padding:12px 16px;color:#fff;font-size:13px">Combined odds ({len(legs)} legs)</td>'
                 f'<td align="right" style="padding:12px 16px;color:#fff;font-size:22px;'
                 f'font-weight:800">{combined_odds:.2f}x</td>'
                 f'</tr></table>')
    else:
        body += f'<p style="color:{GRAY}">No qualifying H/A pick this week — skip Slip 1.</p>'

    # ── SLIP 2 ────────────────────────────────────────────────────────────────
    body += (f'<h2 style="margin:20px 0 14px;font-size:15px;font-weight:800;'
             f'color:{DARK};border-bottom:2px solid #e5e7eb;padding-bottom:8px">'
             f'SLIP 2 — DRAW SINGLE</h2>')
    if best_draw:
        body += pick_card("DRAW", best_draw['match'], "Draw",
                          best_draw['odds'], best_draw['why'], "#7c3aed")
    else:
        body += f'<p style="color:{GRAY}">No qualifying draw this week — skip Slip 2.</p>'

    # ── ALL CANDIDATES ────────────────────────────────────────────────────────
    body += (f'<h2 style="margin:24px 0 14px;font-size:15px;font-weight:800;'
             f'color:{DARK};border-bottom:2px solid #e5e7eb;padding-bottom:8px">'
             f'All Candidates</h2>')

    # H/A table
    if all_ha:
        body += section_title("H/A — all leagues (sorted by confidence)")
        rows = [(p['match'], p.get('league',''), p.get('kickoff','')[:10],
                 p['pick'], f"{p['odds']:.2f}", str(int(p['conf'])))
                for p in all_ha[:8]]
        body += _table(["Match","League","Kickoff","Pick","Odds","Conf"], rows)

    # YC table — 'conf' IS yc_pred in yc_picks
    if all_yc:
        body += section_title("YC Over 3.5 — all leagues (sorted by predicted cards)")
        rows = []
        for p in all_yc[:8]:
            ev_str = f"+{p['ev']:.1%}" if p.get('ev', 0) >= 0 else f"{p['ev']:.1%}"
            rows.append((p['match'], p.get('league',''), p.get('kickoff','')[:10],
                         f"{p.get('conf', p.get('yc_pred', 0)):.1f}",
                         f"{p['odds']:.2f}", ev_str))
        body += _table(["Match","League","Kickoff","YC avg","Odds","EV"], rows)

    # BTTS table — 'conf' = home_gf5 + away_gf5; parse from 'why' string
    if all_btts:
        body += section_title("O2.5 + BTTS — all leagues (sorted by goal output)")
        rows = []
        for p in all_btts[:8]:
            import re as _re
            m = _re.search(r'home_gf5=([\d.]+).*away_gf5=([\d.]+)', p.get('why',''))
            hg = float(m.group(1)) if m else 0.0
            ag = float(m.group(2)) if m else 0.0
            ev_str = f"+{p['ev']:.1%}" if p.get('ev', 0) >= 0 else f"{p['ev']:.1%}"
            odds_str = p.get('ref_note', f"{p['odds']:.2f}")
            rows.append((p['match'], p.get('league',''), p.get('kickoff','')[:10],
                         f"{hg:.1f}", f"{ag:.1f}", odds_str, ev_str))
        body += _table(["Match","League","Kickoff","Home gf5","Away gf5","Est odds","EV"], rows)

    # Footer
    body += (
        f'<div style="margin-top:24px;padding-top:16px;border-top:1px solid #e5e7eb;'
        f'font-size:12px;color:{GRAY};display:flex;justify-content:space-between">'
        f'<span>EV 1.68 · 75% slip win rate (last 27 weeks)</span>'
        f'<span>Good luck! 🍀</span>'
        f'</div>'
    )

    body += '</div>'  # close padding div

    # Wrap in full HTML
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '</head><body style="margin:0;padding:20px;background:#f3f4f6;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        '<div style="max-width:600px;margin:0 auto;border-radius:8px;'
        'overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">'
        + body +
        '</div></body></html>'
    )
    return html


def save_picks_to_db(best_ha, best_leg2, best_leg3, best_draw):
    """Persist this week's live picks to weekly_picks table. INSERT OR IGNORE so re-runs are safe."""
    from datetime import timedelta
    today = datetime.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end   = week_start + timedelta(days=6)           # Sunday
    week_label = f"{week_start.strftime('%Y-%m-%d')}/{week_end.strftime('%Y-%m-%d')}"

    legs = [l for l in [best_ha, best_leg2, best_leg3] if l]
    n_legs = len(legs)
    combined_odds = round(float(pd.Series([l['odds'] for l in legs]).prod()), 2) if legs else None

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
            leg3_market   TEXT, leg3_match TEXT, leg3_pick TEXT,
            leg3_odds     REAL, leg3_why   TEXT, leg3_hit  INTEGER,
            draw_match    TEXT, draw_pick  TEXT,
            draw_odds     REAL, draw_hit   INTEGER
        )
    """)
    # Add leg3 columns if DB was created before this version
    for col in ['leg3_market','leg3_match','leg3_pick','leg3_odds','leg3_why','leg3_hit']:
        try:
            conn.execute(f"ALTER TABLE weekly_picks ADD COLUMN {col} {'REAL' if 'odds' in col else ('INTEGER' if 'hit' in col else 'TEXT')}")
        except Exception:
            pass

    conn.execute("""
        INSERT OR IGNORE INTO weekly_picks
            (week, generated_at, n_legs, combined_odds, slip_won,
             leg1_market, leg1_match, leg1_pick, leg1_odds, leg1_why, leg1_hit,
             leg2_market, leg2_match, leg2_pick, leg2_odds, leg2_why, leg2_hit,
             leg3_market, leg3_match, leg3_pick, leg3_odds, leg3_why, leg3_hit,
             draw_match, draw_pick, draw_odds, draw_hit)
        VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL)
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
        best_leg3.get('market', 'YC Over 3.5') if best_leg3 else None,
        best_leg3['match'] if best_leg3 else None,
        best_leg3['pick']  if best_leg3 else None,
        best_leg3['odds']  if best_leg3 else None,
        best_leg3['why']   if best_leg3 else None,
        best_draw['match'] if best_draw else None,
        'D'                if best_draw else None,
        best_draw['odds']  if best_draw else None,
    ))
    conn.commit()
    conn.close()
    print(f"Picks saved to DB — week {week_label}")


def send_email(subject, html):
    cfg = EMAIL_CONFIG
    to_list = cfg['to_addr'] if isinstance(cfg['to_addr'], list) else [cfg['to_addr']]

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f"Winner Picks <{cfg['from_addr']}>"
    msg['To']      = ', '.join(to_list)
    msg.attach(MIMEText(html, 'html'))

    with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port']) as server:
        server.ehlo()
        server.starttls()
        server.login(cfg['from_addr'], cfg['password'])
        server.sendmail(cfg['from_addr'], to_list, msg.as_string())

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
    best_ha, best_leg2, best_leg3, best_draw, all_ha, all_draws, _yc, _btts = generate_picks()

    # 3. Save picks to DB (permanent record)
    save_picks_to_db(best_ha, best_leg2, best_leg3, best_draw)

    # 4. Format & send
    body = format_email(best_ha, best_leg2, best_leg3, best_draw, all_ha, all_draws, _yc, _btts)
    print("\nEmail HTML generated.")

    today = datetime.today().strftime('%Y-%m-%d')
    send_email(f"Winner Picks {today}", body)
