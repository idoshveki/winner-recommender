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

from recommend.send_weekly import generate_picks, format_email

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Winner Recommender",
    page_icon="⚽",
    layout="wide",
)

st.title("⚽ Winner Recommender")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Actions")
    if st.button("🔄 Refresh odds & picks", use_container_width=True):
        with st.spinner("Fetching latest odds..."):
            venv_py = ROOT / ".venv" / "bin" / "python"
            subprocess.run([str(venv_py), str(ROOT / "src" / "data" / "fetch_odds.py")],
                           cwd=str(ROOT), capture_output=True)
        st.success("Done!")
        st.rerun()

    st.divider()
    st.caption("Model: Combined slip (H/A + 1st Corner)")
    st.caption("EV 1.68 | 75% win rate (27 weeks)")

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📅 This Week", "📊 Past Results", "🗄️ Match History"])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — THIS WEEK'S PICKS
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader(f"Picks for week of {datetime.today().strftime('%B %d, %Y')}")

    with st.spinner("Generating picks..."):
        try:
            best_ha, best_crn, best_draw, all_ha, all_draws = generate_picks()
        except Exception as e:
            st.error(f"Error generating picks: {e}")
            best_ha = best_crn = best_draw = None
            all_ha = all_draws = []

    # ── Slip 1: Combined ──────────────────────────────────────────────────────
    st.markdown("### 🎯 Slip 1 — Combined Bet")
    if best_ha:
        combined_odds = round(best_ha['odds'] * (best_crn['odds'] if best_crn else 1), 2)
        col1, col2, col3 = st.columns(3)
        col1.metric("Combined Odds", f"{combined_odds:.2f}x")
        col2.metric("Legs", 2 if best_crn else 1)
        col3.metric("Expected win rate", "74%")

        st.markdown("#### Leg 1 — H/A Pick")
        c1, c2 = st.columns([2, 1])
        with c1:
            pick_label = "Home Win" if best_ha['pick'] == 'H' else "Away Win"
            st.markdown(f"**{best_ha['match']}** ({best_ha['league']}, {best_ha['kickoff']})")
            st.markdown(f"Pick: **{pick_label} @ {best_ha['odds']:.2f}**")
        with c2:
            st.info(f"Confidence: {best_ha['conf']}")

        with st.expander("Why this pick?"):
            st.markdown(f"_{best_ha['why']}_")
            # Show form context
            conn = sqlite3.connect(DB_PATH)
            ht_name = best_ha['match'].split(' vs ')[0]
            at_name = best_ha['match'].split(' vs ')[1]
            home_form = pd.read_sql(f"""
                SELECT date, away_team AS opponent, home_goals || '-' || away_goals AS score, result
                FROM matches_history
                WHERE home_team = '{ht_name}' AND result IS NOT NULL
                ORDER BY date DESC LIMIT 5
            """, conn)
            away_form = pd.read_sql(f"""
                SELECT date, home_team AS opponent, away_goals || '-' || home_goals AS score, result
                FROM matches_history
                WHERE away_team = '{at_name}' AND result IS NOT NULL
                ORDER BY date DESC LIMIT 5
            """, conn)
            conn.close()
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**{ht_name} — last 5 home games**")
                if not home_form.empty:
                    home_form['result'] = home_form['result'].map({'H': '✅ W', 'D': '➖ D', 'A': '❌ L'})
                    st.dataframe(home_form, hide_index=True, use_container_width=True)
            with col_b:
                st.markdown(f"**{at_name} — last 5 away games**")
                if not away_form.empty:
                    away_form['result'] = away_form['result'].map({'H': '❌ L', 'D': '➖ D', 'A': '✅ W'})
                    st.dataframe(away_form, hide_index=True, use_container_width=True)

        if best_crn:
            st.markdown("#### Leg 2 — First Corner Pick")
            c1, c2 = st.columns([2, 1])
            with c1:
                crn_label = "Home team gets first corner" if best_crn['pick'] == 'H' else "Away team gets first corner"
                st.markdown(f"**{best_crn['match']}** ({best_crn['league']}, {best_crn['kickoff']})")
                st.markdown(f"Pick: **{crn_label} @ {best_crn['odds']:.2f}**")
            with c2:
                st.info(f"Confidence: {best_crn['conf']}")
            with st.expander("Why this pick?"):
                st.markdown(f"_{best_crn['why']}_")

        # Other H/A candidates
        if len(all_ha) > 1:
            with st.expander(f"Other H/A candidates ({len(all_ha)-1} more)"):
                df_ha = pd.DataFrame(all_ha[1:])
                df_ha['pick'] = df_ha['pick'].map({'H': 'Home', 'A': 'Away'})
                st.dataframe(df_ha[['match', 'league', 'kickoff', 'pick', 'odds', 'conf', 'why']],
                             hide_index=True, use_container_width=True)
    else:
        st.warning("No qualifying H/A pick this week — skip Slip 1")

    st.divider()

    # ── Slip 2: Draw ──────────────────────────────────────────────────────────
    st.markdown("### 🎯 Slip 2 — Draw Single")
    if best_draw:
        col1, col2, col3 = st.columns(3)
        col1.metric("Odds", f"{best_draw['odds']:.2f}x")
        col2.metric("Pinnacle draw prob", f"{best_draw['conf']/100:.0%}")
        col3.metric("Expected accuracy", "34%")

        st.markdown(f"**{best_draw['match']}** ({best_draw['league']}, {best_draw['kickoff']})")
        with st.expander("Why this pick?"):
            st.markdown(f"_{best_draw['why']}_")

        if len(all_draws) > 1:
            with st.expander(f"Other draw candidates ({len(all_draws)-1} more)"):
                df_d = pd.DataFrame(all_draws[1:])
                st.dataframe(df_d[['match', 'league', 'kickoff', 'odds', 'conf', 'why']],
                             hide_index=True, use_container_width=True)
    else:
        st.warning("No qualifying draw pick this week — skip Slip 2")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — PAST RESULTS
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Past Results — Current Season")

    csv_path = ROOT / "data" / "reports" / "combined_slip_last20.csv"
    if not csv_path.exists():
        st.warning("No past results file found. Run the backtest first.")
    else:
        df = pd.read_csv(csv_path)

        # Summary metrics
        has_result = df[df['slip_won'].notna() & (df['slip_won'] != '')]
        wins = (has_result['slip_won'] == True).sum()
        total = len(has_result)
        avg_odds = has_result['combined_odds'].dropna().mean()

        draw_df = df[df['draw_hit'].notna() & (df['draw_hit'] != '')]
        draw_wins = (draw_df['draw_hit'] == True).sum()

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Weeks played", total)
        col2.metric("Slip win rate", f"{wins/total:.0%}" if total else "—")
        col3.metric("Avg combined odds", f"{avg_odds:.2f}x" if avg_odds else "—")
        col4.metric("EV", f"{(wins/total)*avg_odds:.2f}" if total and avg_odds else "—")
        col5.metric("Draw accuracy", f"{draw_wins/len(draw_df):.0%}" if len(draw_df) else "—")

        # P&L calculator
        st.markdown("#### 💰 P&L Calculator")
        stake = st.slider("Stake per week (NIS)", 10, 500, 50, step=10)

        balance = 0
        pnl_rows = []
        for _, r in df.iterrows():
            if pd.isna(r.get('slip_won')) or r['slip_won'] == '':
                continue
            if r['slip_won'] == True:
                profit = round(stake * r['combined_odds'] - stake, 1)
            else:
                profit = -stake
            balance += profit
            pnl_rows.append({
                'week': r['week'][:10],
                'src': '📌 Pinnacle' if r.get('odds_source') == 'pinnacle' else '📊 Avg',
                'slip': '✅ WON' if r['slip_won'] else '❌ LOST',
                'odds': r['combined_odds'],
                'P&L': f"+{profit}" if profit > 0 else str(profit),
                'balance': f"+{balance}" if balance >= 0 else str(balance),
            })

        pnl_df = pd.DataFrame(pnl_rows)
        if not pnl_df.empty:
            pnl_df = pnl_df.iloc[::-1].reset_index(drop=True)  # most recent first
            col_a, col_b = st.columns([3.9, 1])
            with col_a:
                st.dataframe(pnl_df, hide_index=True, use_container_width=True,
                             height=min(40 + len(pnl_df) * 35, 900))
            with col_b:
                st.metric("Total staked", f"{total * stake} NIS")
                st.metric("Net profit", f"{'+' if balance >= 0 else ''}{balance} NIS",
                          delta=f"{balance/total/stake*100:.1f}% ROI/week")

        st.divider()

        # Week detail drill-down
        st.markdown("#### 🔍 Drill into a week")
        weeks = df['week'].tolist()[::-1]  # most recent first
        selected = st.selectbox("Select week", weeks, index=0)
        row = df[df['week'] == selected].iloc[0]

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Combined Slip**")
            for i in range(1, 4):
                mkt = row.get(f'leg{i}_market', '')
                if not mkt:
                    break
                hit = '✅' if row[f'leg{i}_hit'] == True else '❌'
                st.markdown(
                    f"{hit} **[{mkt}]** {row[f'leg{i}_match']} — "
                    f"pick **{row[f'leg{i}_pick']}** @ {row[f'leg{i}_odds']}x  \n"
                    f"  _{row[f'leg{i}_why']}_"
                )
            result_label = "✅ WON" if row['slip_won'] == True else ("❌ LOST" if row['slip_won'] == False else "—")
            st.markdown(f"**Result: {result_label} @ {row['combined_odds']}x**")

        with col2:
            st.markdown("**Draw Single**")
            if row.get('draw_match'):
                dhit = '✅' if row['draw_hit'] == True else ('❌' if row['draw_hit'] == False else '—')
                st.markdown(f"{dhit} {row['draw_match']} → Draw @ {row['draw_odds']}x")
            else:
                st.markdown("_No draw pick this week_")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — MATCH HISTORY
# ═══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("Match History DB")

    conn = sqlite3.connect(DB_PATH)

    col1, col2, col3 = st.columns(3)
    leagues = ['All'] + pd.read_sql("SELECT DISTINCT league FROM matches_history ORDER BY league", conn)['league'].tolist()
    sel_league = col1.selectbox("League", leagues)
    sel_season = col2.selectbox("Season", ['All', '2025/26', '2024/25', '2023/24', '2022/23'])
    sel_team   = col3.text_input("Search team", "")

    query = "SELECT date, league, home_team, away_team, home_goals, away_goals, result, ht_result, home_corners, away_corners, pinnacle_prob_h, pinnacle_prob_d, pinnacle_prob_a FROM matches_history WHERE 1=1"
    if sel_league != 'All':
        query += f" AND league = '{sel_league}'"
    if sel_season != 'All':
        year = int(sel_season[:4])
        query += f" AND date >= '{year}-08-01' AND date < '{year+1}-07-01'"
    if sel_team:
        query += f" AND (home_team LIKE '%{sel_team}%' OR away_team LIKE '%{sel_team}%')"
    query += " ORDER BY date DESC LIMIT 200"

    hist = pd.read_sql(query, conn)
    conn.close()

    st.caption(f"Showing {len(hist)} matches")
    st.dataframe(hist, hide_index=True, use_container_width=True)
