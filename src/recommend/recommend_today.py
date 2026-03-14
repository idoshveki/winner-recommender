"""
Daily recommendation engine — V5 model.
Reads upcoming fixtures from odds_raw + form from matches_history.
Outputs:
  1. ACCUMULATOR: top 2-3 H/A picks across EPL/Bundesliga/Serie_A/La_Liga
  2. DRAW SINGLE: 1 best draw pick per week
Saves to data/reports/YYYY-MM-DD_recommendation.md
"""

import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT       = Path(__file__).resolve().parents[2]
DB_PATH    = ROOT / "data" / "db" / "winner.db"
REPORT_DIR = ROOT / "data" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ── Team name mapping: odds_raw → matches_history ────────────────────────────
NAME_MAP = {
    # EPL
    "Brighton and Hove Albion": "Brighton",
    "Wolverhampton Wanderers":  "Wolves",
    "Manchester City":          "Man City",
    "Manchester United":        "Man United",
    "Tottenham Hotspur":        "Tottenham",
    "Newcastle United":         "Newcastle",
    "Nottingham Forest":        "Nott'm Forest",
    "Leeds United":             "Leeds",
    "West Ham United":          "West Ham",
    "Sunderland":               "Sunderland",
    "Crystal Palace":           "Crystal Palace",
    # Bundesliga
    "Borussia Monchengladbach": "M'gladbach",
    "FC St. Pauli":             "St Pauli",
    "Hamburger SV":             "Hamburg",
    "Bayer Leverkusen":         "Leverkusen",
    "Borussia Dortmund":        "Dortmund",
    "Eintracht Frankfurt":      "Ein Frankfurt",
    "TSG Hoffenheim":           "Hoffenheim",
    "VfB Stuttgart":            "Stuttgart",
    "VfL Wolfsburg":            "Wolfsburg",
    "SC Freiburg":              "Freiburg",
    "FSV Mainz 05":             "Mainz",
    "1. FC Heidenheim":         "Heidenheim",
    "1. FC Köln":               "FC Koln",
    "Union Berlin":             "Union Berlin",
    "Werder Bremen":            "Werder Bremen",
    "RB Leipzig":               "RB Leipzig",
    "Augsburg":                 "Augsburg",
    "Bayern Munich":            "Bayern Munich",
    # Serie A
    "AC Milan":                 "Milan",
    "Inter Milan":              "Inter",
    "AS Roma":                  "Roma",
    "Atalanta BC":              "Atalanta",
    "Hellas Verona":            "Verona",
    # La Liga
    "Atlético Madrid":          "Ath Madrid",
    "Athletic Bilbao":          "Ath Bilbao",
    "Real Sociedad":            "Sociedad",
    "Real Betis":               "Betis",
    "CA Osasuna":               "Osasuna",
    "Alavés":                   "Alaves",
    "Celta Vigo":               "Celta",
    "Rayo Vallecano":           "Vallecano",
    "Elche CF":                 "Elche",
    "Espanyol":                 "Espanol",
}

SPORT_TO_LEAGUE = {
    "soccer_epl":                "EPL",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a":      "Serie_A",
    "soccer_spain_la_liga":      "La_Liga",
}

UNRELIABLE_HOME = {'Tottenham', 'Man United', 'Chelsea', 'Brighton', 'West Ham', 'Bournemouth'}


def norm(name: str) -> str:
    return NAME_MAP.get(name, name)


# ── Form calculator from match history ───────────────────────────────────────

def get_team_form(conn, team: str, league: str, before_date: str):
    """
    Load all matches for a team before a date, return feature dict.
    Separate home/away venue stats.
    """
    rows = conn.execute("""
        SELECT date, home_team, away_team, home_goals, away_goals, result
        FROM matches_history
        WHERE league = ? AND date < ?
          AND (home_team = ? OR away_team = ?)
        ORDER BY date
    """, (league, before_date, team, team)).fetchall()

    if not rows:
        return None

    all_games, home_games, away_games = [], [], []
    for date, ht, at, hg, ag, res in rows:
        if ht == team:
            g_for, g_ag = hg or 0, ag or 0
            r = 'W' if hg > ag else 'D' if hg == ag else 'L'
            home_games.append((date, g_for, g_ag, r))
        else:
            g_for, g_ag = ag or 0, hg or 0
            r = 'W' if ag > hg else 'D' if ag == hg else 'L'
            away_games.append((date, g_for, g_ag, r))
        all_games.append((date, g_for, g_ag, r))

    def pts(hist, n):
        h = hist[-n:]
        return sum(3 if r=='W' else 1 if r=='D' else 0 for _,_,_,r in h)

    def gf_avg(hist, n):
        h = hist[-n:]
        return sum(g for _,g,_,_ in h) / max(len(h), 1)

    def ga_avg(hist, n):
        h = hist[-n:]
        return sum(g for _,_,g,_ in h) / max(len(h), 1)

    def draw_rate(hist, n):
        h = hist[-n:]
        return sum(1 for _,_,_,r in h if r=='D') / max(len(h), 1)

    def trend(hist):
        recent = pts(hist, 3)
        prior  = pts(hist[-6:-3], 3) if len(hist) >= 6 else pts(hist, 3)
        return recent - prior

    def winstreak(hist):
        s = 0
        for entry in reversed(hist):
            if entry[3] == 'W': s += 1
            else: break
        return s

    def losestreak(hist):
        s = 0
        for entry in reversed(hist):
            if entry[3] == 'L': s += 1
            else: break
        return s

    return {
        "pts5":        pts(all_games, 5),
        "pts10":       pts(all_games, 10),
        "gf5":         round(gf_avg(all_games, 5), 2),
        "ga5":         round(ga_avg(all_games, 5), 2),
        "dr10":        round(draw_rate(all_games, 10), 3),
        "trend":       trend(all_games),
        "winstreak":   winstreak(all_games),
        "losestreak":  losestreak(all_games),
        "venue_pts5":  pts(home_games, 5),   # used for home team
        "venue_away_pts5": pts(away_games, 5),  # used for away team
        "n_games":     len(all_games),
    }


# ── Odds loader ───────────────────────────────────────────────────────────────

def get_upcoming_fixtures(conn, days: int = 7):
    """Load upcoming fixtures with consensus Pinnacle/avg odds from odds_raw."""
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Get one row per event to identify fixtures
    events = conn.execute("""
        SELECT DISTINCT sport, event_id, home_team, away_team, commence_time
        FROM odds_raw
        WHERE sport IN (
            'soccer_epl','soccer_germany_bundesliga',
            'soccer_italy_serie_a','soccer_spain_la_liga'
        )
          AND commence_time >= ?
          AND commence_time <= ?
        ORDER BY commence_time
    """, (now, cutoff)).fetchall()

    fixtures = []
    for sport, event_id, home_team, away_team, kickoff in events:
        # Get Pinnacle odds first, fallback to average across bookmakers
        for bookmaker_filter in ["pinnacle", None]:
            q = """
                SELECT outcome_name, AVG(price) as avg_price
                FROM odds_raw
                WHERE event_id = ? AND market = 'h2h'
            """
            params = [event_id]
            if bookmaker_filter:
                q += " AND bookmaker = ?"
                params.append(bookmaker_filter)
            q += " GROUP BY outcome_name"

            rows = conn.execute(q, params).fetchall()
            if rows and len(rows) == 3:
                break

        if not rows or len(rows) < 3:
            continue

        odds_map = {r[0]: r[1] for r in rows}
        # Find home/away/draw — home_team is the key for home odds
        raw_h = odds_map.get(home_team)
        raw_a = odds_map.get(away_team)
        raw_d = odds_map.get("Draw")

        if not all([raw_h, raw_a, raw_d]):
            # Try partial match
            for name, price in odds_map.items():
                if "draw" in name.lower():
                    raw_d = price
                elif raw_h is None:
                    raw_h = price
                else:
                    raw_a = price

        if not all([raw_h, raw_a, raw_d]):
            continue

        # Remove vig
        implied = [1/raw_h, 1/raw_d, 1/raw_a]
        total   = sum(implied)
        ph, pd_, pa = [x/total for x in implied]

        fixtures.append({
            "sport":       sport,
            "league":      SPORT_TO_LEAGUE[sport],
            "event_id":    event_id,
            "home_team":   home_team,
            "away_team":   away_team,
            "kickoff":     kickoff,
            "pinnacle_h":  round(raw_h, 2),
            "pinnacle_d":  round(raw_d, 2),
            "pinnacle_a":  round(raw_a, 2),
            "pinnacle_prob_h": round(ph, 4),
            "pinnacle_prob_d": round(pd_, 4),
            "pinnacle_prob_a": round(pa, 4),
        })

    return fixtures


# ── Scorers (mirror accumulator.py v5 logic) ──────────────────────────────────

def score_ha(row: dict) -> tuple:
    """Returns (pick, confidence, odds) or (None, 0, None)."""
    ph, pa      = row["pinnacle_prob_h"], row["pinnacle_prob_a"]
    venue_gap   = row.get("venue_gap", row.get("pts5_diff", 0))
    pts5_diff   = row.get("pts5_diff", 0)
    h_trend     = row.get("home_trend", 0)
    a_trend     = row.get("away_trend", 0)
    h_win       = row.get("home_winstreak", 0)
    a_lose      = row.get("away_losestreak", 0)
    a_win       = row.get("away_winstreak", 0)
    h_lose      = row.get("home_losestreak", 0)

    # HOME
    if ph >= 0.63 and venue_gap >= 5 and pts5_diff >= 5 and h_trend >= 0:
        if not (row["home_team"] in UNRELIABLE_HOME and ph < 0.72):
            score = ph * 10 * (1 + venue_gap / 25)
            if h_win >= 3:   score *= 1.25
            elif h_win >= 2: score *= 1.10
            if a_lose >= 2:  score *= 1.15
            if row.get("home_gf5", 0) > 1.8 and row.get("away_ga5", 0) > 1.5:
                score *= 1.20
            if row.get("home_pts5", 0) >= 12 and row.get("away_pts5", 0) <= 3:
                score *= 1.50
            if h_trend == 0 and a_trend > 1:
                score *= 0.85
            if score >= 7:
                return ("H", round(score, 1), row["pinnacle_h"])

    # AWAY
    if pa >= 0.58 and venue_gap <= -5 and pts5_diff <= -5 and a_trend >= 0:
        score = pa * 10 * (1 + abs(venue_gap) / 25)
        if a_win >= 3:   score *= 1.25
        elif a_win >= 2: score *= 1.10
        if h_lose >= 2:  score *= 1.15
        if row.get("away_gf5", 0) > 1.8 and row.get("home_ga5", 0) > 1.5:
            score *= 1.20
        if row.get("away_pts5", 0) >= 12 and row.get("home_pts5", 0) <= 3:
            score *= 1.50
        if score >= 6:
            return ("A", round(score, 1), row["pinnacle_a"])

    return (None, 0, None)


def score_draw(row: dict) -> tuple:
    """Returns (confidence, odds) or (0, None)."""
    pd_ = row["pinnacle_prob_d"]
    gap = row.get("pts5_diff", 0)
    hdr = row.get("home_dr10", 0)
    adr = row.get("away_dr10", 0)
    hgf = row.get("home_gf5", 0)
    agf = row.get("away_gf5", 0)
    hga = row.get("home_ga5", 0)
    aga = row.get("away_ga5", 0)

    if pd_ < 0.28 or abs(gap) > 3 or hdr < 0.20 or adr < 0.20:
        return 0, None

    draw_pool = (hdr + adr) / 2
    score = pd_ * draw_pool * 100
    if pd_ > 0.32:   score *= 1.20
    elif pd_ > 0.30: score *= 1.10
    if gap == 0:     score *= 1.10
    if hgf < 1.4 and agf < 1.4:
        score *= 1.10

    return round(score, 1), row["pinnacle_d"]


# ── Explanation builder ───────────────────────────────────────────────────────

def explain(row: dict, pick: str) -> str:
    ph  = row["pinnacle_prob_h"]
    pd_ = row["pinnacle_prob_d"]
    pa  = row["pinnacle_prob_a"]

    if pick == "H":
        parts = [f"Pinnacle: home {ph:.0%} win probability"]
        vg = row.get("venue_gap", 0)
        if vg >= 7: parts.append(f"dominant home-venue form gap: {vg:+.0f}pts")
        elif vg >= 5: parts.append(f"home stronger at home than away is on the road: +{vg:.0f}pts")
        if row.get("home_pts5", 0) >= 12 and row.get("away_pts5", 0) <= 3:
            parts.append(f"MISMATCH: home {row['home_pts5']:.0f}pts vs away {row['away_pts5']:.0f}pts last 5")
        if row.get("home_winstreak", 0) >= 2:
            parts.append(f"home on {row['home_winstreak']}-game win streak")
        if row.get("away_losestreak", 0) >= 2:
            parts.append(f"away on {row['away_losestreak']}-game losing streak")
        if row.get("home_gf5", 0) > 1.8 and row.get("away_ga5", 0) > 1.5:
            parts.append(f"attacking mismatch: home {row['home_gf5']:.1f} goals/game, away concedes {row['away_ga5']:.1f}")
        return " | ".join(parts)

    elif pick == "A":
        parts = [f"Pinnacle: away {pa:.0%} win probability"]
        vg = row.get("venue_gap", 0)
        if vg <= -7: parts.append(f"dominant away-venue form gap: {vg:+.0f}pts")
        elif vg <= -5: parts.append(f"away stronger on road than home is at home: {vg:.0f}pts")
        if row.get("away_pts5", 0) >= 12 and row.get("home_pts5", 0) <= 3:
            parts.append(f"MISMATCH: away {row['away_pts5']:.0f}pts vs home {row['home_pts5']:.0f}pts last 5")
        if row.get("away_winstreak", 0) >= 2:
            parts.append(f"away on {row['away_winstreak']}-game win streak")
        if row.get("home_losestreak", 0) >= 2:
            parts.append(f"home on {row['home_losestreak']}-game losing streak")
        if row.get("away_gf5", 0) > 1.8 and row.get("home_ga5", 0) > 1.5:
            parts.append(f"away attack {row['away_gf5']:.1f} goals/game vs leaky home defence {row['home_ga5']:.1f}")
        return " | ".join(parts)

    elif pick == "D":
        parts = [f"Pinnacle draw prob {pd_:.0%} (odds {row['pinnacle_d']:.2f})"]
        parts.append(f"tight form: gap={row.get('pts5_diff',0):+.0f}pts")
        parts.append(f"both draw-prone: home {row.get('home_dr10',0):.0%}, away {row.get('away_dr10',0):.0%} last 10")
        hgf = row.get("home_gf5", 0); agf = row.get("away_gf5", 0)
        if hgf < 1.4 and agf < 1.4:
            parts.append(f"low-scoring teams ({hgf:.1f} + {agf:.1f} goals/game) — expect tight game")
        return " | ".join(parts)

    return ""


# ── Main report generator ─────────────────────────────────────────────────────

def generate_recommendation(days_ahead: int = 7, min_form_games: int = 5):
    conn = sqlite3.connect(DB_PATH)
    fixtures = get_upcoming_fixtures(conn, days=days_ahead)
    print(f"Found {len(fixtures)} upcoming fixtures across 4 leagues")

    enriched = []
    for fix in fixtures:
        league    = fix["league"]
        kickoff   = fix["kickoff"][:10]
        home_hist = norm(fix["home_team"])
        away_hist = norm(fix["away_team"])

        hf = get_team_form(conn, home_hist, league, kickoff)
        af = get_team_form(conn, away_hist, league, kickoff)

        if hf is None or af is None or hf["n_games"] < min_form_games or af["n_games"] < min_form_games:
            continue

        row = {
            **fix,
            "home_team_hist": home_hist,
            "away_team_hist": away_hist,
            "home_pts5":      hf["pts5"],
            "home_pts10":     hf["pts10"],
            "home_gf5":       hf["gf5"],
            "home_ga5":       hf["ga5"],
            "home_dr10":      hf["dr10"],
            "home_trend":     hf["trend"],
            "home_winstreak": hf["winstreak"],
            "home_losestreak":hf["losestreak"],
            "away_pts5":      af["pts5"],
            "away_pts10":     af["pts10"],
            "away_gf5":       af["gf5"],
            "away_ga5":       af["ga5"],
            "away_dr10":      af["dr10"],
            "away_trend":     af["trend"],
            "away_winstreak": af["winstreak"],
            "away_losestreak":af["losestreak"],
            "pts5_diff":      hf["pts5"] - af["pts5"],
            "venue_gap":      hf["venue_pts5"] - af["venue_away_pts5"],
        }

        pick, conf, odds = score_ha(row)
        row["pick"]       = pick
        row["confidence"] = conf
        row["bet_odds"]   = odds

        dconf, dodds = score_draw(row)
        row["draw_conf"] = dconf
        row["draw_odds"] = dodds

        enriched.append(row)

    conn.close()

    # ── Accumulator picks: top 3 H/A by confidence ───────────────────────────
    ha_picks = sorted(
        [r for r in enriched if r["pick"] is not None],
        key=lambda x: x["confidence"], reverse=True
    )[:3]

    # ── Draw single: highest confidence draw pick ─────────────────────────────
    draw_picks = sorted(
        [r for r in enriched if r["draw_conf"] > 0],
        key=lambda x: x["draw_conf"], reverse=True
    )
    draw_pick = draw_picks[0] if draw_picks else None

    # ── Markdown report ───────────────────────────────────────────────────────
    today  = datetime.now().strftime("%Y-%m-%d")
    lines  = [
        f"# Winner Recommendations — {today}",
        f"",
        f"Model: V5 | Leagues: EPL, Bundesliga, Serie A, La Liga",
        f"Backtest: Accumulator EV=1.040 (49.3% win rate) | Draw singles EV=1.025 (32.5% acc @ 3.16x)",
        f"",
    ]

    # ── Section 1: Accumulator ────────────────────────────────────────────────
    lines += [
        f"---",
        f"",
        f"## ACCUMULATOR (H/A picks)",
        f"",
    ]
    if not ha_picks:
        lines.append("_No qualifying picks this week — criteria not met. Skip accumulator._")
    else:
        combo = 1.0
        for r in ha_picks:
            combo *= r["bet_odds"]
        lines += [
            f"**{len(ha_picks)} legs | Combined odds: {combo:.2f}x**",
            f"",
        ]
        for i, r in enumerate(ha_picks, 1):
            pick_label = "Home Win" if r["pick"] == "H" else "Away Win"
            lines += [
                f"### Leg {i}: {r['home_team']} vs {r['away_team']} ({r['league']}, {r['kickoff'][:10]})",
                f"**Pick: {pick_label} @ {r['bet_odds']:.2f}**",
                f"",
                f"| Signal | Home | Away |",
                f"|--------|------|------|",
                f"| Pinnacle prob | {r['pinnacle_prob_h']:.0%} | {r['pinnacle_prob_a']:.0%} (draw {r['pinnacle_prob_d']:.0%}) |",
                f"| Overall form (last 5) | {r['home_pts5']}pts | {r['away_pts5']}pts |",
                f"| Venue form (last 5) | {r.get('home_team_hist','')} at home: {hf_venue(r)}pts | {r.get('away_team_hist','')} away: {af_venue(r)}pts |",
                f"| Goals scored/game | {r['home_gf5']:.1f} | {r['away_gf5']:.1f} |",
                f"| Goals conceded/game | {r['home_ga5']:.1f} | {r['away_ga5']:.1f} |",
                f"| Form trend (+improving) | {r['home_trend']:+d} | {r['away_trend']:+d} |",
                f"| Win streak | {r['home_winstreak']} | {r['away_winstreak']} |",
                f"| Lose streak | {r['home_losestreak']} | {r['away_losestreak']} |",
                f"",
                f"**Why:** {explain(r, r['pick'])}",
                f"",
                f"**Confidence score:** {r['confidence']}",
                f"",
            ]

    # ── Section 2: Draw Single ────────────────────────────────────────────────
    lines += [
        f"---",
        f"",
        f"## DRAW SINGLE",
        f"",
    ]
    if not draw_pick:
        lines.append("_No qualifying draw pick this week._")
    else:
        r = draw_pick
        lines += [
            f"### {r['home_team']} vs {r['away_team']} ({r['league']}, {r['kickoff'][:10]})",
            f"**Pick: Draw @ {r['draw_odds']:.2f}**",
            f"",
            f"| Signal | Home | Away |",
            f"|--------|------|------|",
            f"| Pinnacle draw prob | {r['pinnacle_prob_d']:.0%} | — |",
            f"| Overall form (last 5) | {r['home_pts5']}pts | {r['away_pts5']}pts |",
            f"| Draw rate (last 10) | {r['home_dr10']:.0%} | {r['away_dr10']:.0%} |",
            f"| Goals scored/game | {r['home_gf5']:.1f} | {r['away_gf5']:.1f} |",
            f"| Goals conceded/game | {r['home_ga5']:.1f} | {r['away_ga5']:.1f} |",
            f"",
            f"**Why:** {explain(r, 'D')}",
            f"",
            f"**Confidence score:** {r['draw_conf']}",
            f"",
        ]

    # ── Other candidates (runner-up draws + lower-conf H/A) ───────────────────
    runner_up_ha = sorted(
        [r for r in enriched if r["pick"] is not None and r not in ha_picks],
        key=lambda x: x["confidence"], reverse=True
    )[:5]
    runner_up_draws = draw_picks[1:4] if len(draw_picks) > 1 else []

    if runner_up_ha or runner_up_draws:
        lines += [f"---", f"", f"## Other candidates (did not meet top-3 threshold)", f""]
        for r in runner_up_ha:
            label = "Home Win" if r["pick"]=="H" else "Away Win"
            lines.append(
                f"- **{r['home_team']} vs {r['away_team']}** ({r['league']}) — "
                f"{label} @ {r['bet_odds']:.2f} | conf={r['confidence']} | "
                f"form {r['home_pts5']}vs{r['away_pts5']}pts | Pinnacle {r['pinnacle_prob_h']:.0%}/{r['pinnacle_prob_d']:.0%}/{r['pinnacle_prob_a']:.0%}"
            )
        if runner_up_draws:
            lines.append("")
            lines.append("**Runner-up draw candidates:**")
            for r in runner_up_draws:
                lines.append(
                    f"- **{r['home_team']} vs {r['away_team']}** ({r['league']}) — "
                    f"Draw @ {r['draw_odds']:.2f} | conf={r['draw_conf']} | "
                    f"Pinnacle draw {r['pinnacle_prob_d']:.0%} | form gap {r['pts5_diff']:+.0f}pts"
                )

    out_path = REPORT_DIR / f"{today}_recommendation.md"
    out_path.write_text("\n".join(lines))
    print(f"\nSaved: {out_path}")
    return {"ha_picks": ha_picks, "draw_pick": draw_pick, "path": str(out_path)}


def hf_venue(r): return r.get("venue_gap", 0) + r.get("away_pts5", 0)  # reconstruct
def af_venue(r): return r.get("home_pts5", 0) - r.get("venue_gap", 0)  # reconstruct


if __name__ == "__main__":
    result = generate_recommendation(days_ahead=7)
    print(f"\nAccumulator picks: {len(result['ha_picks'])}")
    for r in result["ha_picks"]:
        print(f"  {r['home_team']} vs {r['away_team']} → {r['pick']} @ {r['bet_odds']:.2f} (conf={r['confidence']})")
    if result["draw_pick"]:
        dp = result["draw_pick"]
        print(f"Draw single: {dp['home_team']} vs {dp['away_team']} → Draw @ {dp['draw_odds']:.2f} (conf={dp['draw_conf']})")
