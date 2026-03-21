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
        st.cache_data.clear()
        st.success("Done!")
        st.rerun()

    if st.button("Clear picks cache", use_container_width=True):
        st.cache_data.clear()
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
            best_ha, best_leg2, best_leg3, best_draw, all_ha, all_draws, all_yc, all_btts = generate_picks()
        except Exception as e:
            st.error(f"Error generating picks: {e}")
            best_ha = best_leg2 = best_draw = None
            all_ha = all_draws = all_yc = all_btts = []

    col_s1, col_stretch = st.columns(2)

    # ── Slip 1 ────────────────────────────────────────────────────────────────
    with col_s1:
        st.markdown("**Slip 1 — Combined**")
        if best_ha:
            legs = [l for l in [best_ha, best_leg2, best_leg3] if l]
            combined_odds = round(float(__import__('pandas').Series([l['odds'] for l in legs]).prod()), 2)
            st.metric("Combined odds", f"{combined_odds:.2f}× ({len(legs)} legs)")

            leg2_tag = best_leg2.get('market', '2nd Leg') if best_leg2 else '2nd Leg'
            leg3_tag = best_leg3.get('market', '3rd Leg') if best_leg3 else ''
            leg2_detail = (
                f"{best_leg2['league']} · {best_leg2['kickoff']} · {best_leg2['pick']} @ {best_leg2['odds']:.2f}"
                if best_leg2 else 'No qualifying second leg'
            )
            leg3_html = f"""
  <div class="leg-row">
    <span class="tag">{leg3_tag}</span> <b>{best_leg3['match']}</b><br>
    <small>{best_leg3['league']} · {best_leg3['kickoff']} · {best_leg3['pick']} @ {best_leg3['odds']:.2f}</small>
  </div>""" if best_leg3 else ""
            st.markdown(f"""
<div class="slip-box">
  <div class="leg-row">
    <span class="tag">H/A</span> <b>{best_ha['match']}</b><br>
    <small>{best_ha['league']} · {best_ha['kickoff']} · Pick: {'Home' if best_ha['pick']=='H' else 'Away'} @ {best_ha['odds']:.2f}</small>
  </div>
  <div class="leg-row">
    <span class="tag">{leg2_tag}</span> <b>{best_leg2['match'] if best_leg2 else '—'}</b><br>
    <small>{leg2_detail}</small>
  </div>{leg3_html}
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

            # ── All candidates ────────────────────────────────────────────────
            st.divider()
            st.markdown("**All candidates**")

            if all_ha:
                st.caption("H/A — all leagues (sorted by confidence)")
                ha_rows = [{'Match': p['match'], 'League': p['league'],
                            'Kickoff': p['kickoff'],
                            'Pick': 'Home' if p['pick'] == 'H' else 'Away',
                            'Odds': p['odds'], 'Pin prob %': round(p['conf'] / 10 * 100)}
                           for p in all_ha[:5]]
                st.dataframe(pd.DataFrame(ha_rows), hide_index=True,
                             use_container_width=True)

            if all_yc:
                st.caption("YC Over 3.5 — all leagues (sorted by predicted cards)")
                yc_rows = [{'Match': p['match'], 'League': p['league'],
                             'Kickoff': p['kickoff'], 'YC avg': p['conf'], 'Odds': p['odds']}
                           for p in all_yc[:5]]
                st.dataframe(pd.DataFrame(yc_rows), hide_index=True,
                             use_container_width=True)
            else:
                st.caption("No YC candidates this week.")

            if all_btts:
                st.caption("O2.5 + BTTS — all leagues (sorted by goal output)")
                btts_rows = [{'Match': p['match'], 'League': p['league'],
                               'Kickoff': p['kickoff'],
                               'Home gf5': float(p['why'].split('home_gf5=')[1].split(' ')[0]),
                               'Away gf5': float(p['why'].split('away_gf5=')[1]),
                               'Avg goals': p['conf'], 'Odds': p['odds']}
                             for p in all_btts[:5]]
                st.dataframe(pd.DataFrame(btts_rows), hide_index=True,
                             use_container_width=True)
            else:
                st.caption("No O2.5+BTTS candidates this week.")
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
    st.markdown("### Past Results — Live Picks")

    # ── Load from DB (weekly_picks table) ────────────────────────────────────
    _conn = sqlite3.connect(DB_PATH)
    _conn.execute("""
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
    _conn.commit()
    df = pd.read_sql("SELECT * FROM weekly_picks ORDER BY week", _conn)
    _conn.close()

    # ── Add this week as pending if picks generated but not yet saved to DB ──
    from datetime import timedelta as _td
    _today = pd.Timestamp.today()
    _week_start = _today - pd.Timedelta(days=_today.weekday())
    _week_end   = _week_start + pd.Timedelta(days=6)
    current_week_label = f"{_week_start.strftime('%Y-%m-%d')}/{_week_end.strftime('%Y-%m-%d')}"

    already_saved = current_week_label in df['week'].values
    if best_ha and not already_saved:
        _legs = [l for l in [best_ha, best_leg2, best_leg3] if l]
        combined_odds = round(float(pd.Series([l['odds'] for l in _legs]).prod()), 2)
        pending_row = {
            'week': current_week_label, 'generated_at': None,
            'n_legs': len(_legs), 'combined_odds': combined_odds,
            'slip_won': None,
            'leg1_market': 'H/A', 'leg1_match': best_ha['match'],
            'leg1_pick': best_ha['pick'], 'leg1_odds': best_ha['odds'],
            'leg1_why': best_ha['why'], 'leg1_hit': None,
            'leg2_market': best_leg2.get('market', '') if best_leg2 else None,
            'leg2_match': best_leg2['match'] if best_leg2 else None,
            'leg2_pick':  best_leg2['pick']  if best_leg2 else None,
            'leg2_odds':  best_leg2['odds']  if best_leg2 else None,
            'leg2_why':   best_leg2['why']   if best_leg2 else None,
            'leg2_hit': None,
            'draw_match': best_draw['match'] if best_draw else None,
            'draw_pick':  'D'               if best_draw else None,
            'draw_odds':  best_draw['odds'] if best_draw else None,
            'draw_hit': None,
        }
        df = pd.concat([df, pd.DataFrame([pending_row])], ignore_index=True)

    if df.empty:
        st.info("No picks recorded yet. Run send_weekly.py to generate this week's picks.")
    else:
        # ── Summary metrics ───────────────────────────────────────────────────
        has_result = df[df['slip_won'].notna()]
        wins  = (has_result['slip_won'] == 1).sum()
        total = len(has_result)
        avg_odds = has_result['combined_odds'].dropna().mean()

        stake = st.slider("Stake per week (NIS)", 10, 500, 50, step=10)

        # Running P&L
        balance = 0
        balance_series = []
        for _, r in df.iterrows():
            won = r.get('slip_won')
            if won == 1 or won is True:
                balance += round(stake * r['combined_odds'] - stake, 1)
            elif won == 0 or won is False:
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
        chart_df = df[df['slip_won'].notna()].copy()
        chart_df['week_short'] = chart_df['week'].str[:10]
        if not chart_df.empty:
            st.line_chart(chart_df.set_index('week_short')['_balance'], height=160,
                          use_container_width=True)

        st.divider()

        # ── Mark Results ──────────────────────────────────────────────────────
        pending_weeks = df[df['slip_won'].isna()]['week'].tolist()
        if pending_weeks:
            with st.expander("Mark Results", expanded=True):
                sel_week = st.selectbox("Week", pending_weeks, index=len(pending_weeks)-1)
                row = df[df['week'] == sel_week].iloc[0]
                col_a, col_b, col_c = st.columns(3)
                l1_res = col_a.radio(
                    f"Leg 1 — {row['leg1_match']} ({row['leg1_pick']} @ {row['leg1_odds']}×)",
                    ['Pending', 'Hit ✅', 'Miss ❌'], horizontal=True,
                    key='l1_res'
                )
                l2_res = col_b.radio(
                    f"Leg 2 — {row['leg2_match'] or '—'} ({row.get('leg2_market','')} @ {row['leg2_odds'] or '—'}×)",
                    ['Pending', 'Hit ✅', 'Miss ❌'], horizontal=True,
                    key='l2_res'
                ) if row.get('leg2_match') else 'Pending'
                draw_res = col_c.radio(
                    f"Draw — {row['draw_match'] or '—'} @ {row['draw_odds'] or '—'}×",
                    ['Pending', 'Hit ✅', 'Miss ❌'], horizontal=True,
                    key='draw_res'
                ) if row.get('draw_match') else 'Pending'
                slip_res = st.radio("Slip result", ['Pending', 'Won ✅', 'Lost ❌'], horizontal=True, key='slip_res')

                if st.button("Save Results"):
                    def _to_int(val):
                        if 'Hit' in val or 'Won' in val: return 1
                        if 'Miss' in val or 'Lost' in val: return 0
                        return None
                    _c = sqlite3.connect(DB_PATH)
                    _c.execute("""
                        UPDATE weekly_picks
                        SET slip_won=?, leg1_hit=?, leg2_hit=?, draw_hit=?
                        WHERE week=?
                    """, (_to_int(slip_res), _to_int(l1_res), _to_int(l2_res), _to_int(draw_res), sel_week))
                    _c.commit()
                    _c.close()
                    st.success(f"Results saved for {sel_week}")
                    st.rerun()

        st.divider()

        # ── Full results timeline ─────────────────────────────────────────────
        st.markdown("#### Week by week")

        timeline_rows = []
        running_bal = 0
        for _, r in df.iterrows():
            won = r.get('slip_won')
            is_pending = (won is None or pd.isna(won) if won != won else False)

            def _hit_icon(v):
                if v == 1 or v is True: return '✅'
                if v == 0 or v is False: return '❌'
                return '⏳'

            l1_pick_disp = 'Home' if r.get('leg1_pick') == 'H' else ('Away' if r.get('leg1_pick') == 'A' else str(r.get('leg1_pick', '')))
            l1_cell = f"{_hit_icon(r.get('leg1_hit'))} {r.get('leg1_match','')}\n{l1_pick_disp} @ {r.get('leg1_odds','')}×"

            l2_match = r.get('leg2_match') or ''
            l2_cell = f"{_hit_icon(r.get('leg2_hit'))} [{r.get('leg2_market','')}] {l2_match}\n@ {r.get('leg2_odds','')}×" if l2_match else '—'

            draw_match = r.get('draw_match') or ''
            draw_cell = f"{_hit_icon(r.get('draw_hit'))} {draw_match} @ {r.get('draw_odds','')}×" if draw_match else '—'

            try:
                is_pending = pd.isna(won)
            except Exception:
                is_pending = won is None

            if is_pending:
                slip_cell = f"⏳ Pending @ {r['combined_odds']}×"
                pnl_cell = '—'
                bal_cell = f"{'+' if running_bal >= 0 else ''}{running_bal:.0f}"
            elif won == 1 or won is True:
                profit = round(stake * r['combined_odds'] - stake, 1)
                running_bal += profit
                slip_cell = f"✅ WON @ {r['combined_odds']}×"
                pnl_cell = f"+{profit:.0f}"
                bal_cell = f"{'+' if running_bal >= 0 else ''}{running_bal:.0f}"
            else:
                running_bal -= stake
                slip_cell = f"❌ LOST @ {r['combined_odds']}×"
                pnl_cell = f"-{stake}"
                bal_cell = f"{'+' if running_bal >= 0 else ''}{running_bal:.0f}"

            timeline_rows.append({
                'Week': str(r['week'])[:10],
                'Leg 1 — H/A': l1_cell,
                'Leg 2': l2_cell,
                'Draw': draw_cell,
                'Slip': slip_cell,
                'P&L': pnl_cell,
                'Balance': bal_cell,
            })

        tl_df = pd.DataFrame(timeline_rows).iloc[::-1].reset_index(drop=True)
        st.dataframe(
            tl_df,
            hide_index=True,
            use_container_width=True,
            height=min(50 + len(tl_df) * 38, 900),
            column_config={
                'Week':         st.column_config.TextColumn(width='small'),
                'Leg 1 — H/A': st.column_config.TextColumn(width='large'),
                'Leg 2':        st.column_config.TextColumn(width='large'),
                'Draw':         st.column_config.TextColumn(width='medium'),
                'Slip':         st.column_config.TextColumn(width='medium'),
                'P&L':          st.column_config.TextColumn(width='small'),
                'Balance':      st.column_config.TextColumn(width='small'),
            }
        )

        # ── Reasoning drill-down ───────────────────────────────────────────────
        st.divider()
        st.markdown("#### Reasoning")
        all_weeks = df['week'].tolist()[::-1]
        sel_reason_week = st.selectbox("Select week to see reasoning", all_weeks,
                                       key='reason_week_sel',
                                       format_func=lambda w: str(w)[:10])
        reason_row = df[df['week'] == sel_reason_week].iloc[0]
        r = reason_row

        col_r1, col_r2, col_r3 = st.columns(3)
        with col_r1:
            st.markdown(f"**Leg 1 — H/A**")
            st.markdown(f"`{r.get('leg1_match','—')}` · {r.get('leg1_pick','')} @ {r.get('leg1_odds','')}×")
            if r.get('leg1_why'):
                st.caption(r['leg1_why'])
        with col_r2:
            st.markdown(f"**Leg 2 — {r.get('leg2_market','') or '—'}**")
            if r.get('leg2_match'):
                st.markdown(f"`{r['leg2_match']}` @ {r.get('leg2_odds','')}×")
                if r.get('leg2_why'):
                    st.caption(r['leg2_why'])
            else:
                st.markdown("—")
        with col_r3:
            st.markdown("**Draw**")
            if r.get('draw_match'):
                st.markdown(f"`{r['draw_match']}` @ {r.get('draw_odds','')}×")
            else:
                st.markdown("—")


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
