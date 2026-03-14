"""
Master feature pipeline.
Combines all 7 feature sources into a single match context dict,
then computes a blended probability estimate (1/X/2).
"""

import sqlite3
import time
from pathlib import Path

from src.data.fetch_sofascore import team_form, TOURNAMENT_IDS
from src.features.h2h import get_h2h
from src.features.standings import standing_summary
from src.features.weather import get_weather
from src.features.news import get_team_news
from src.features.xg import get_team_xg
from src.features.rest_days import get_rest_days, rest_label
from src.features.home_away_form import get_split_form

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "db" / "winner.db"


# ── Odds from DB ─────────────────────────────────────────────────────────────

def get_odds_implied(home_team: str, away_team: str) -> dict:
    """Pull consensus implied probability from our odds DB (Pinnacle preferred)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        # Try Pinnacle first, then average all bookmakers
        for bm_filter in ["WHERE bookmaker='pinnacle'", ""]:
            rows = conn.execute(f"""
                SELECT outcome_name, AVG(price)
                FROM odds_raw
                WHERE market='h2h'
                AND (
                    (home_team LIKE ? AND away_team LIKE ?)
                    OR (home_team LIKE ? AND away_team LIKE ?)
                )
                {bm_filter}
                GROUP BY outcome_name
            """, (
                f"%{home_team[:6]}%", f"%{away_team[:6]}%",
                f"%{away_team[:6]}%", f"%{home_team[:6]}%",
            )).fetchall()

            if rows:
                break
        conn.close()

        if not rows:
            return {}

        odds_map = {r[0]: r[1] for r in rows}

        # Convert decimal odds → implied probability, then remove vig
        raw = {}
        for outcome, odds in odds_map.items():
            if odds and odds > 1:
                raw[outcome] = 1 / odds

        total_overround = sum(raw.values())
        if total_overround == 0:
            return {}

        # Keys may be team names or "Draw"
        p_home = p_draw = p_away = None
        for name, prob in raw.items():
            norm = prob / total_overround
            if "draw" in name.lower():
                p_draw = norm
            elif any(w in name.lower() for w in home_team.lower().split()[:2]):
                p_home = norm
            else:
                p_away = norm

        if p_home and p_draw and p_away:
            return {"p_home": round(p_home, 3), "p_draw": round(p_draw, 3),
                    "p_away": round(p_away, 3), "source": "odds_db"}
    except Exception:
        pass
    return {}


# ── Form-based probability ───────────────────────────────────────────────────

def form_prob(home_form: dict, away_form: dict, home_split=None, away_split=None) -> tuple:
    """
    Blended form probability using overall + home/away split form.
    Returns (p_home, p_draw, p_away).
    """
    # Use home-specific form for home team if available
    hp = home_form["points"]
    ap = away_form["points"]

    if home_split and home_split["home"]["played"] >= 3:
        hp = (hp + home_split["home"]["points"]) / 2

    if away_split and away_split["away"]["played"] >= 3:
        ap = (ap + away_split["away"]["points"]) / 2

    hp += 0.5  # home advantage
    total = hp + ap + 1.5
    p_home = hp / total
    p_away = ap / total
    p_draw = max(0.18, 1.0 - p_home - p_away)  # draw floor at 18%
    s = p_home + p_draw + p_away
    return round(p_home / s, 3), round(p_draw / s, 3), round(p_away / s, 3)


# ── xG-based probability ─────────────────────────────────────────────────────

def xg_prob(home_xg: dict, away_xg: dict) -> tuple:
    """Poisson-based probability from xG data."""
    import math

    def ppf(k, lam):
        return (lam ** k) * math.exp(-lam) / math.factorial(k)

    hxg = home_xg.get("xg_for") or 1.3
    axg = away_xg.get("xg_for") or 1.1
    hdef = home_xg.get("xg_against") or 1.1
    adef = away_xg.get("xg_against") or 1.3

    # Expected goals each team scores (blend attack xG vs opponent's xGA)
    lambda_home = (hxg + adef) / 2 * 1.05  # slight home boost
    lambda_away = (axg + hdef) / 2

    p_home = p_draw = p_away = 0.0
    for i in range(8):
        for j in range(8):
            p = ppf(i, max(lambda_home, 0.1)) * ppf(j, max(lambda_away, 0.1))
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p

    s = p_home + p_draw + p_away
    if s == 0:
        return 0.45, 0.27, 0.28
    return round(p_home / s, 3), round(p_draw / s, 3), round(p_away / s, 3)


# ── Blend all signals ────────────────────────────────────────────────────────

def blend_probabilities(odds, form, xg, h2h, standings, news_home, news_away) -> tuple:
    """
    Weighted blend of all probability sources.
    Weights reflect reliability: odds > xG > form > h2h > news/standings.
    """
    weights = []
    probs   = []

    if odds.get("p_home"):
        probs.append((odds["p_home"], odds["p_draw"], odds["p_away"]))
        weights.append(0.45)  # bookmakers are the best single signal

    if xg[0] is not None:
        probs.append(xg)
        weights.append(0.25)

    if form[0] is not None:
        probs.append(form)
        weights.append(0.20)

    if h2h.get("home_win_pct") is not None and h2h["total"] >= 3:
        probs.append((h2h["home_win_pct"], h2h["draw_pct"], h2h["away_win_pct"]))
        weights.append(0.10)

    if not probs:
        return 0.40, 0.27, 0.33

    total_w = sum(weights)
    p_home = sum(p[0] * w for p, w in zip(probs, weights)) / total_w
    p_draw = sum(p[1] * w for p, w in zip(probs, weights)) / total_w
    p_away = sum(p[2] * w for p, w in zip(probs, weights)) / total_w

    # News sentiment adjustment (±3% nudge)
    if news_home.get("sentiment") == "negative":
        p_home = max(0.05, p_home - 0.03)
    elif news_home.get("sentiment") == "positive":
        p_home = min(0.95, p_home + 0.03)

    if news_away.get("sentiment") == "negative":
        p_away = max(0.05, p_away - 0.03)
    elif news_away.get("sentiment") == "positive":
        p_away = min(0.95, p_away + 0.03)

    # H2H standing adjustment for standings gap
    sh = standings.get("home", {})
    sa = standings.get("away", {})
    if sh.get("position") and sa.get("position"):
        pos_gap = sa["position"] - sh["position"]  # positive = home ranked higher
        nudge = min(0.05, abs(pos_gap) * 0.005)
        if pos_gap > 3:
            p_home = min(0.95, p_home + nudge)
        elif pos_gap < -3:
            p_away = min(0.95, p_away + nudge)

    # Renormalize
    s = p_home + p_draw + p_away
    return round(p_home / s, 3), round(p_draw / s, 3), round(p_away / s, 3)


# ── Main pipeline entry ───────────────────────────────────────────────────────

def build_match_features(event: dict, date: str) -> dict:
    """
    Full feature build for one match event.
    Returns a rich context dict + final blended probabilities.
    """
    home = event.get("homeTeam", {})
    away = event.get("awayTeam", {})
    home_id   = home.get("id")
    away_id   = away.get("id")
    home_name = home.get("name", "?")
    away_name = away.get("name", "?")
    event_id  = event.get("id")
    tourn_uid = event.get("tournament", {}).get("uniqueTournament", {}).get("id")
    tournament = TOURNAMENT_IDS.get(tourn_uid, "?")

    ctx = {
        "home": home_name, "away": away_name,
        "tournament": tournament, "event_id": event_id,
    }

    # 1. Overall form (existing)
    try:
        hf = team_form(home_id, date)
        af = team_form(away_id, date)
    except Exception:
        hf = af = {"points": 0, "played": 0, "form_str": "", "wins": 0, "draws": 0,
                   "losses": 0, "goals_for": 0, "goals_against": 0}
    ctx["home_form"] = hf
    ctx["away_form"] = af
    time.sleep(0.3)

    # 2. Home/Away split form
    try:
        home_split = get_split_form(home_id, date)
        away_split = get_split_form(away_id, date)
    except Exception:
        home_split = away_split = None
    ctx["home_split"] = home_split
    ctx["away_split"] = away_split
    time.sleep(0.3)

    # 3. Rest days
    try:
        home_rest = get_rest_days(home_id, date)
        away_rest = get_rest_days(away_id, date)
    except Exception:
        home_rest = away_rest = None
    ctx["home_rest_days"] = home_rest
    ctx["away_rest_days"] = away_rest
    ctx["home_rest_label"] = rest_label(home_rest)
    ctx["away_rest_label"] = rest_label(away_rest)
    time.sleep(0.3)

    # 4. H2H
    try:
        h2h = get_h2h(event_id)
    except Exception:
        h2h = {"total": 0, "home_win_pct": None, "draw_pct": None, "away_win_pct": None,
               "avg_goals_home": None, "avg_goals_away": None}
    ctx["h2h"] = h2h
    time.sleep(0.3)

    # 5. Standings
    try:
        standings = standing_summary(home_id, away_id, tourn_uid)
    except Exception:
        standings = {"home": {}, "away": {}}
    ctx["standings"] = standings
    time.sleep(0.3)

    # 6. Weather
    try:
        weather = get_weather(home_name, date)
    except Exception:
        weather = {"rain_mm": 0, "wind_kmh": 0, "heavy_rain": False,
                   "strong_wind": False, "weather_impact": "unknown"}
    ctx["weather"] = weather

    # 7. News sentiment
    try:
        news_home = get_team_news(home_name, date)
        news_away = get_team_news(away_name, date)
    except Exception:
        from src.features.news import _empty_news
        news_home = _empty_news(home_name)
        news_away = _empty_news(away_name)
    ctx["news_home"] = news_home
    ctx["news_away"] = news_away

    # 8. xG (Understat, top 5 leagues only)
    try:
        xg_home = get_team_xg(home_name, tourn_uid, date)
        xg_away = get_team_xg(away_name, tourn_uid, date)
    except Exception:
        from src.features.xg import _empty_xg
        xg_home = xg_away = _empty_xg()
    ctx["xg_home"] = xg_home
    ctx["xg_away"] = xg_away

    # 9. Odds from DB
    odds_implied = get_odds_implied(home_name, away_name)
    ctx["odds_implied"] = odds_implied

    # ── Compute probabilities ─────────────────────────────────────────────
    form_p = form_prob(hf, af, home_split, away_split)
    xg_p   = xg_prob(xg_home, xg_away) if xg_home.get("xg_for") else (None, None, None)

    p1, px, p2 = blend_probabilities(
        odds_implied, form_p, xg_p, h2h, standings, news_home, news_away
    )
    ctx["p_home"] = p1
    ctx["p_draw"] = px
    ctx["p_away"] = p2

    # Recommendation
    best = max(p1, px, p2)
    if best == p1:
        ctx["recommendation"] = "1 (Home Win)"
        ctx["confidence"] = round(p1 * 100, 1)
    elif best == px:
        ctx["recommendation"] = "X (Draw)"
        ctx["confidence"] = round(px * 100, 1)
    else:
        ctx["recommendation"] = "2 (Away Win)"
        ctx["confidence"] = round(p2 * 100, 1)

    return ctx
