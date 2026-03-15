"""
Winner Recommender Dashboard
Run with: streamlit run app.py
"""

import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "db" / "winner.db"
sys.path.insert(0, str(ROOT / "src"))

from recommend.send_weekly import generate_picks as _generate_picks

@st.cache_data(ttl=3600, show_spinner=False)
def generate_picks():
    return _generate_picks()

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Winner Recommender",
    page_icon="⚽",
    layout="wide",
)

# ── Clean minimal styling ─────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.5px; }
    h2 { font-size: 1.2rem; font-weight: 600; }
    h3 { font-size: 1.05rem; font-weight: 600; }
    .slip-box {
        border: 1px solid rgba(128,128,128,0.25);
        border-radius: 8px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 1rem;
        background: rgba(128,128,128,0.07);
    }
    .leg-row {
        padding: 0.5rem 0;
        border-bottom: 1px solid rgba(128,128,128,0.15);
        font-size: 0.95rem;
    }
    .leg-row:last-child { border-bottom: none; }
    .tag {
        display: inline-block;
        font-size: 0.72rem;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 4px;
        background: rgba(128,128,128,0.18);
        color: inherit;
        margin-right: 6px;
        letter-spacing: 0.3px;
    }
    .stretch-tag { background: rgba(128,128,128,0.12); opacity: 0.75; }
    .metric-label { font-size: 0.78rem; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }
    .metric-value { font-size: 1.5rem; font-weight: 700; }
    div[data-testid="stMetric"] { background: rgba(128,128,128,0.07); border-radius: 8px; padding: 0.7rem 1rem; }
    div[data-testid="stMetricValue"] { font-size: 1.4rem; }
    .stTabs [data-baseweb="tab"] { font-size: 0.9rem; font-weight: 500; }
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

st.markdown("## ⚽ Winner Recommender")
st.caption(f"Model v6 · Combined slip (H/A + YC Over 3.5) · EV 1.57 · 67% win rate over 27 weeks")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Actions")
    if st.button("Refresh odds & picks", use_container_width=True):
        with st.spinner("Fetching latest odds..."):
            venv_py = ROOT / ".venv" / "bin" / "python"
            subprocess.run([str(venv_py), str(ROOT / "src" / "data" / "fetch_odds.py")],
                           cwd=str(ROOT), capture_output=True)
        st.success("Done!")
        st.rerun()

    st.divider()
    st.markdown("**Slip structure**")
    st.markdown("- Slip 1: H/A + YC Over 3.5 / O2.5+BTTS / 2nd H/A")
    st.markdown("- Stretch: optional H/A single + draw")
    st.divider()

    with st.expander("📖 Term glossary"):
        st.markdown("""
**ph / pa / pd**
Pinnacle implied probability for Home win / Away win / Draw. Vig-removed so they sum to 100%. Pinnacle is the sharpest bookmaker — their probabilities are the most accurate signal.

---

**venue_gap**
Home team's points in their last 5 *home* games minus the away team's points in their last 5 *away* games. Measures form specifically at each team's venue. A gap of +5 or more means the home side is significantly stronger on their own ground.

---

**pts5_diff**
Home team's overall points (last 5 games, all venues) minus the away team's overall points. Secondary form signal. Must align with venue_gap for a pick to qualify.

---

**Confidence score**
A weighted score for each pick starting from Pinnacle probability × 10, then multiplied by bonuses: win streak, opponent losing streak, attacking mismatch, form dominance. Higher = stronger pick. The accumulator only fires when the weakest leg is ≥ 13.

---

**streak**
How many consecutive wins the home team has in their home games (for H picks), or consecutive wins the away team has in away games (for A picks). A streak of 3+ gives a 1.25× confidence bonus.

---

**lstreak**
Losing streak of the *opponent*. E.g. if the away team has lost their last 3 away games, that's an lstreak of 3. Gives a 1.15× confidence bonus to the home pick.

---

**trend**
Points in the last 3 games minus points in the 3 games before that. Positive = improving form, negative = declining. A team must have trend ≥ 0 to qualify (no declining teams).

---

**draw rate (dr10)**
Fraction of the last 10 games that ended in a draw, per team. Both teams must be above 20% for a draw pick to qualify. Teams with high draw rates tend to play more cautious, balanced football.

---

**YC Over 3.5 (La Liga)**
Predicted total yellow cards for a La Liga match, computed from each team's rolling 5-game YC averages at their respective venues (home team's home games + away team's away games). If the combined prediction is ≥ 3.5, we bet Over 3.5 yellow cards @ 1.85x. Base rate 68% in La Liga, EV 1.262. Always available on 1win (Over 4.5 is not consistently offered).

---

**O2.5+BTTS (Over 2.5 And Yes)**
Both Teams to Score AND total goals over 2.5. Qualifies when home team averages ≥ 1.8 goals/home game and away team averages ≥ 1.5 goals/away game. Used as second leg fallback when no YC pick qualifies. Odds 2.63x. Hit rate 49% when filter met → EV 1.284. Always available on 1win under "Total and both teams to score".

---

**EV (Expected Value)**
Average return per unit staked over many bets. EV > 1.0 means profitable long-term. E.g. EV 1.68 means for every 100 NIS staked you expect 168 NIS back on average.

---

**📌 vs 📊**
📌 = Pinnacle closing odds used (most accurate, historical data)
📊 = Market average odds used (recent weeks where Pinnacle closing odds not yet published)
""")

    st.divider()
    st.caption("Data: football-data.co.uk + The Odds API")

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["This Week", "Past Results", "Match History"])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — THIS WEEK
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown(f"### Week of {datetime.today().strftime('%d %B %Y')}")

    with st.spinner("Generating picks..."):
        try:
            best_ha, best_leg2, best_draw, all_ha, all_draws = generate_picks()
        except Exception as e:
            st.error(f"Error generating picks: {e}")
            best_ha = best_leg2 = best_draw = None
            all_ha = all_draws = []

    col_s1, col_stretch = st.columns(2)

    # ── Slip 1 ────────────────────────────────────────────────────────────────
    with col_s1:
        st.markdown("**Slip 1 — Combined**")
        if best_ha:
            combined_odds = round(best_ha['odds'] * (best_leg2['odds'] if best_leg2 else 1), 2)
            st.metric("Combined odds", f"{combined_odds:.2f}×")

            leg2_tag = best_leg2.get('market', '2nd Leg') if best_leg2 else '2nd Leg'
            leg2_detail = (
                f"{best_leg2['league']} · {best_leg2['kickoff']} · {best_leg2['pick']} @ {best_leg2['odds']:.2f}"
                if best_leg2 else 'No qualifying second leg'
            )
            st.markdown(f"""
<div class="slip-box">
  <div class="leg-row">
    <span class="tag">H/A</span> <b>{best_ha['match']}</b><br>
    <small>{best_ha['league']} · {best_ha['kickoff']} · Pick: {'Home' if best_ha['pick']=='H' else 'Away'} @ {best_ha['odds']:.2f}</small>
  </div>
  <div class="leg-row">
    <span class="tag">{leg2_tag}</span> <b>{best_leg2['match'] if best_leg2 else '—'}</b><br>
    <small>{leg2_detail}</small>
  </div>
</div>
""", unsafe_allow_html=True)

            with st.expander("Why Leg 1 (H/A)?"):
                st.markdown(f"_{best_ha['why']}_  \nConfidence: **{best_ha['conf']}**")
                # Recent form
                conn = sqlite3.connect(DB_PATH)
                ht_name = best_ha['match'].split(' vs ')[0]
                at_name = best_ha['match'].split(' vs ')[1]
                home_form = pd.read_sql(f"""
                    SELECT date, away_team AS opponent,
                           home_goals || '-' || away_goals AS score, result
                    FROM matches_history WHERE home_team = '{ht_name}'
                    AND result IS NOT NULL ORDER BY date DESC LIMIT 5
                """, conn)
                away_form = pd.read_sql(f"""
                    SELECT date, home_team AS opponent,
                           away_goals || '-' || home_goals AS score, result
                    FROM matches_history WHERE away_team = '{at_name}'
                    AND result IS NOT NULL ORDER BY date DESC LIMIT 5
                """, conn)
                conn.close()
                c1, c2 = st.columns(2)
                with c1:
                    st.caption(f"{ht_name} — last 5 home")
                    home_form['result'] = home_form['result'].map({'H':'W','D':'D','A':'L'})
                    st.dataframe(home_form, hide_index=True, use_container_width=True)
                with c2:
                    st.caption(f"{at_name} — last 5 away")
                    away_form['result'] = away_form['result'].map({'H':'L','D':'D','A':'W'})
                    st.dataframe(away_form, hide_index=True, use_container_width=True)

            if best_leg2:
                with st.expander(f"Why Leg 2 ({leg2_tag})?"):
                    st.markdown(f"_{best_leg2['why']}_  \nConfidence: **{best_leg2['conf']}**")
        else:
            st.warning("No qualifying H/A pick — skip Slip 1")

    # ── Stretch + Draw ────────────────────────────────────────────────────────
    with col_stretch:
        st.markdown("**Stretch — Optional**")
        st.caption("Lower-conviction picks — bet smaller or skip.")

        if len(all_ha) > 1:
            stretch = all_ha[1]
            st.markdown(f"""
<div class="slip-box">
  <div class="leg-row">
    <span class="tag stretch-tag">H/A</span> <b>{stretch['match']}</b><br>
    <small>{stretch['league']} · {stretch['kickoff']} · {'Home' if stretch['pick']=='H' else 'Away'} @ {stretch['odds']:.2f}</small>
  </div>
</div>
""", unsafe_allow_html=True)
            with st.expander("Why this H/A pick?"):
                st.markdown(f"_{stretch['why']}_  \nConfidence: **{stretch['conf']}** _(lower than primary)_")

        if best_draw:
            st.markdown(f"""
<div class="slip-box">
  <div class="leg-row">
    <span class="tag stretch-tag">Draw</span> <b>{best_draw['match']}</b><br>
    <small>{best_draw['league']} · {best_draw['kickoff']} · Draw @ {best_draw['odds']:.2f}</small>
  </div>
</div>
""", unsafe_allow_html=True)
            with st.expander("Why this draw?"):
                st.markdown(f"_{best_draw['why']}_  \nConfidence: **{best_draw['conf']}**")

        if len(all_ha) <= 1 and not best_draw:
            st.info("No stretch candidates this week.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — PAST RESULTS
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown("### Past Results — Current Season")

    csv_path = ROOT / "data" / "reports" / "combined_slip_last20.csv"
    if not csv_path.exists():
        st.warning("No past results file found. Run the backtest first.")
    else:
        df = pd.read_csv(csv_path)

        # Add this week as pending if not already present
        current_week_label = str(pd.Timestamp.today().to_period('W'))
        already_has_picks = (
            df['week'].eq(current_week_label) & df['n_legs'].fillna(0).gt(0)
        ).any()
        if best_ha and not already_has_picks:
            df = df[df['week'] != current_week_label]
            combined_odds = round(best_ha['odds'] * (best_leg2['odds'] if best_leg2 else 1), 2)
            pending_row = {
                'week': current_week_label,
                'odds_source': 'pinnacle',
                'slip_won': None,
                'combined_odds': combined_odds,
                'n_legs': 2 if best_leg2 else 1,
                'leg1_market': 'H/A', 'leg1_match': best_ha['match'],
                'leg1_pick': best_ha['pick'], 'leg1_odds': best_ha['odds'],
                'leg1_why': best_ha['why'], 'leg1_hit': None,
                'leg2_market': best_leg2.get('market', '') if best_leg2 else '',
                'leg2_match': best_leg2['match'] if best_leg2 else '',
                'leg2_pick': best_leg2['pick'] if best_leg2 else '',
                'leg2_odds': best_leg2['odds'] if best_leg2 else '',
                'leg2_why': best_leg2['why'] if best_leg2 else '',
                'leg2_hit': None,
                'leg3_market': '', 'leg3_match': '', 'leg3_pick': '',
                'leg3_odds': '', 'leg3_why': '', 'leg3_hit': '',
                'draw_match': best_draw['match'] if best_draw else '',
                'draw_pick': 'D' if best_draw else '',
                'draw_odds': best_draw['odds'] if best_draw else '',
                'draw_hit': None,
            }
            df = pd.concat([df, pd.DataFrame([pending_row])], ignore_index=True)

        # ── Summary metrics ───────────────────────────────────────────────────
        has_result = df[df['slip_won'].notna() & (df['slip_won'] != '')]
        wins  = (has_result['slip_won'] == True).sum()
        total = len(has_result)
        avg_odds = has_result['combined_odds'].dropna().mean()

        stake = st.slider("Stake per week (NIS)", 10, 500, 50, step=10)

        # Running P&L
        balance = 0
        balance_series = []
        for _, r in df.iterrows():
            won = r.get('slip_won')
            if won is True or won == True:
                balance += round(stake * r['combined_odds'] - stake, 1)
            elif won is False or won == False:
                balance -= stake
            balance_series.append(balance)
        df['_balance'] = balance_series

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Weeks played", total)
        c2.metric("Win rate", f"{wins/total:.0%}" if total else "—")
        c3.metric("Avg odds", f"{avg_odds:.2f}×" if avg_odds else "—")
        c4.metric("EV", f"{(wins/total)*avg_odds:.2f}" if total and avg_odds else "—")
        c5.metric("Net P&L", f"{'+' if balance >= 0 else ''}{balance:.0f} NIS",
                  delta=f"{balance/(total*stake)*100:.1f}% ROI" if total else None)

        # ── Running balance chart ─────────────────────────────────────────────
        chart_df = df[df['slip_won'].notna() & (df['slip_won'] != '')].copy()
        chart_df['week_short'] = chart_df['week'].str[:10]
        st.line_chart(chart_df.set_index('week_short')['_balance'], height=160,
                      use_container_width=True)

        st.divider()

        # ── Full results timeline ─────────────────────────────────────────────
        st.markdown("#### What really happened — week by week")

        timeline_rows = []
        running_bal = 0
        for _, r in df.iterrows():
            won = r.get('slip_won')
            is_pending = (won is None or won == '')

            # Leg 1
            l1_hit = r.get('leg1_hit')
            l1_icon = '✅' if l1_hit is True else ('❌' if l1_hit is False else '⏳')
            l1_pick_disp = 'Home' if r.get('leg1_pick') == 'H' else ('Away' if r.get('leg1_pick') == 'A' else str(r.get('leg1_pick', '')))
            l1_cell = f"{l1_icon} {r.get('leg1_match','')}\n{l1_pick_disp} @ {r.get('leg1_odds','')}×"

            # Leg 2
            l2_hit = r.get('leg2_hit')
            l2_icon = '✅' if l2_hit is True else ('❌' if l2_hit is False else '⏳')
            l2_market = r.get('leg2_market', '')
            l2_match = r.get('leg2_match', '')
            l2_odds = r.get('leg2_odds', '')
            l2_cell = f"{l2_icon} [{l2_market}] {l2_match}\n@ {l2_odds}×" if l2_match else '—'

            # Slip result
            if is_pending:
                slip_cell = f"⏳ Pending\n@ {r['combined_odds']}×"
                pnl_cell = '—'
                bal_cell = f"{'+' if running_bal >= 0 else ''}{running_bal:.0f}"
            elif won is True or won == True:
                profit = round(stake * r['combined_odds'] - stake, 1)
                running_bal += profit
                slip_cell = f"✅ WON\n@ {r['combined_odds']}×"
                pnl_cell = f"+{profit:.0f}"
                bal_cell = f"{'+' if running_bal >= 0 else ''}{running_bal:.0f}"
            else:
                running_bal -= stake
                slip_cell = f"❌ LOST\n@ {r['combined_odds']}×"
                pnl_cell = f"-{stake}"
                bal_cell = f"{'+' if running_bal >= 0 else ''}{running_bal:.0f}"

            src = '📌' if r.get('odds_source') == 'pinnacle' else '📊'
            timeline_rows.append({
                'Week': f"{src} {str(r['week'])[:10]}",
                'Leg 1 — H/A': l1_cell,
                'Leg 2 — YC/BTTS': l2_cell,
                'Slip': slip_cell,
                'P&L': pnl_cell,
                'Balance': bal_cell,
            })

        tl_df = pd.DataFrame(timeline_rows).iloc[::-1].reset_index(drop=True)
        st.dataframe(
            tl_df,
            hide_index=True,
            use_container_width=True,
            height=min(50 + len(tl_df) * 52, 1100),
            column_config={
                'Week':            st.column_config.TextColumn(width='small'),
                'Leg 1 — H/A':    st.column_config.TextColumn(width='large'),
                'Leg 2 — YC/BTTS':st.column_config.TextColumn(width='large'),
                'Slip':            st.column_config.TextColumn(width='medium'),
                'P&L':             st.column_config.TextColumn(width='small'),
                'Balance':         st.column_config.TextColumn(width='small'),
            }
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — MATCH HISTORY
# ═══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### Match History")

    conn = sqlite3.connect(DB_PATH)
    leagues = ['All'] + pd.read_sql(
        "SELECT DISTINCT league FROM matches_history ORDER BY league", conn
    )['league'].tolist()

    col1, col2, col3 = st.columns(3)
    sel_league = col1.selectbox("League", leagues)
    sel_season = col2.selectbox("Season", ['All', '2025/26', '2024/25', '2023/24', '2022/23'])
    sel_team   = col3.text_input("Search team", "")

    query = """
        SELECT date, league, home_team, away_team,
               home_goals || '-' || away_goals AS score,
               result, ht_result,
               home_corners, away_corners,
               ROUND(pinnacle_prob_h, 2) AS pin_ph,
               ROUND(pinnacle_prob_d, 2) AS pin_pd,
               ROUND(pinnacle_prob_a, 2) AS pin_pa
        FROM matches_history WHERE 1=1
    """
    if sel_league != 'All':
        query += f" AND league = '{sel_league}'"
    if sel_season != 'All':
        year = int(sel_season[:4])
        query += f" AND date >= '{year}-08-01' AND date < '{year+1}-07-01'"
    if sel_team:
        query += f" AND (home_team LIKE '%{sel_team}%' OR away_team LIKE '%{sel_team}%')"
    query += " ORDER BY date DESC LIMIT 300"

    hist = pd.read_sql(query, conn)
    conn.close()

    st.caption(f"{len(hist)} matches shown")
    st.dataframe(hist, hide_index=True, use_container_width=True, height=600)
