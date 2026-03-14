"""
Generate a daily recommendation report using all 7 feature sources.
Explains each pick and scores it against the actual result.
"""

from datetime import datetime
from pathlib import Path
from src.data.fetch_sofascore import get_events_by_date
from src.features.pipeline import build_match_features

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def actual_result(event: dict):
    status = event.get("status", {}).get("type", "")
    if status != "finished":
        return None, None
    hs  = event.get("homeScore", {}).get("current")
    as_ = event.get("awayScore", {}).get("current")
    if hs is None or as_ is None:
        return None, None
    score = f"{hs}-{as_}"
    if hs > as_:
        return "1 (Home Win)", score
    elif hs == as_:
        return "X (Draw)", score
    else:
        return "2 (Away Win)", score


def _weather_note(weather: dict) -> str:
    parts = []
    if weather.get("heavy_rain"):
        parts.append(f"heavy rain ({weather['rain_mm']}mm)")
    if weather.get("strong_wind"):
        parts.append(f"strong wind ({weather['wind_kmh']} km/h)")
    if not parts:
        return f"clear ({weather.get('wind_kmh', 0)} km/h wind, {weather.get('rain_mm', 0)}mm rain)"
    return ", ".join(parts) + " — may suppress goals"


def _news_note(news: dict) -> str:
    s = news.get("sentiment", "unknown")
    injuries = news.get("injury_mentions", [])
    note = f"sentiment: {s}"
    if injuries:
        note += f" | injuries: {injuries[0]}"
    return note


def _build_reason(ctx: dict) -> str:
    reasons = []
    rec = ctx["recommendation"]
    odds = ctx.get("odds_implied", {})
    hf   = ctx["home_form"]
    af   = ctx["away_form"]
    h2h  = ctx["h2h"]
    xgh  = ctx["xg_home"]
    xga  = ctx["xg_away"]
    sh   = ctx["standings"].get("home", {})
    sa   = ctx["standings"].get("away", {})
    nh   = ctx["news_home"]
    na   = ctx["news_away"]

    # Odds signal
    if odds.get("p_home"):
        if rec == "1 (Home Win)":
            reasons.append(f"Bookmaker odds favour home ({odds['p_home']*100:.0f}% implied)")
        elif rec == "2 (Away Win)":
            reasons.append(f"Bookmaker odds favour away ({odds['p_away']*100:.0f}% implied)")
        else:
            reasons.append(f"Odds see this as tight ({odds['p_draw']*100:.0f}% draw implied)")
    else:
        reasons.append("No odds data — using form only")

    # xG signal
    if xgh.get("xg_for") and xga.get("xg_for"):
        reasons.append(
            f"xG: home {xgh['xg_for']}/game (npxG {xgh.get('npxg_for','?')}) "
            f"vs away {xga['xg_for']}/game (npxG {xga.get('npxg_for','?')})"
        )
        if xgh.get("finishing_edge") is not None:
            fe = xgh["finishing_edge"]
            if abs(fe) > 0.2:
                label = "clinical finishers" if fe > 0 else "wasteful in front of goal"
                reasons.append(f"Home team are {label} (finishing edge: {fe:+.2f})")
        if xgh.get("press_intensity") not in (None, "unknown"):
            reasons.append(f"Home pressing: {xgh['press_intensity']} (PPDA {xgh.get('ppda','?')})")

    # Form signal
    reasons.append(
        f"Form: {ctx['home']} {hf['form_str']} ({hf['points']}pts) "
        f"vs {ctx['away']} {af['form_str']} ({af['points']}pts)"
    )

    # Home/Away split
    hs = ctx.get("home_split")
    as_ = ctx.get("away_split")
    if hs and hs["home"]["played"] >= 3:
        reasons.append(
            f"Home-specific form: {hs['home']['form_str']} ({hs['home']['points']}pts in {hs['home']['played']} home games)"
        )
    if as_ and as_["away"]["played"] >= 3:
        reasons.append(
            f"Away-specific form: {as_['away']['form_str']} ({as_['away']['points']}pts in {as_['away']['played']} away games)"
        )

    # H2H
    if h2h.get("total", 0) >= 3:
        reasons.append(
            f"H2H (last {h2h['total']}): home wins {h2h['home_wins']}, "
            f"draws {h2h['draws']}, away wins {h2h['away_wins']}"
        )

    # Standings
    if sh.get("position") and sa.get("position"):
        reasons.append(
            f"League standing: {ctx['home']} #{sh['position']} ({sh.get('points','?')}pts) "
            f"vs {ctx['away']} #{sa['position']} ({sa.get('points','?')}pts)"
        )

    # Rest days
    hr = ctx.get("home_rest_label", "unknown")
    ar = ctx.get("away_rest_label", "unknown")
    if hr != "unknown" or ar != "unknown":
        reasons.append(f"Rest: home {hr} ({ctx.get('home_rest_days','?')} days), away {ar} ({ctx.get('away_rest_days','?')} days)")

    # News
    if nh.get("sentiment") != "unknown":
        reasons.append(f"Home news: {_news_note(nh)}")
    if na.get("sentiment") != "unknown":
        reasons.append(f"Away news: {_news_note(na)}")

    return "\n  - ".join([""] + reasons).lstrip("\n")


def generate_report(date: str) -> dict:
    print(f"\n{'='*60}\n  Generating report: {date}\n{'='*60}")

    events = get_events_by_date(date)
    if not events:
        print("  No events found.")
        return {}

    results = []
    for event in events:
        home_name = event.get("homeTeam", {}).get("name", "?")
        away_name = event.get("awayTeam", {}).get("name", "?")
        tourn = event.get("tournament", {}).get("uniqueTournament", {}).get("id")
        print(f"  {home_name} vs {away_name}...")

        try:
            ctx = build_match_features(event, date)
        except Exception as e:
            print(f"    SKIP: {e}")
            continue

        actual, score = actual_result(event)
        hit = (ctx["recommendation"] == actual) if actual else None

        ctx["actual"] = actual
        ctx["score"]  = score
        ctx["hit"]    = hit
        results.append(ctx)

    # ── Markdown report ───────────────────────────────────────────────────
    hits  = [r for r in results if r["hit"] is True]
    total = len([r for r in results if r["hit"] is not None])
    acc   = round(len(hits) / total * 100, 1) if total else 0

    lines = [
        f"# Recommendation Report — {date}",
        f"",
        f"**Model:** Odds + xG (npxG, PPDA, xPTS) + Home/Away form split + H2H + Standings + Rest days + Weather + News sentiment",
        f"",
        f"## Summary",
        f"- Matches: {len(results)}  |  Finished: {total}  |  Correct: **{len(hits)} ({acc}%)**",
        f"- Baseline (always home): ~45%",
        f"",
        f"---",
    ]

    for r in results:
        xgh = r["xg_home"]
        xga = r["xg_away"]
        sh  = r["standings"].get("home", {})
        sa  = r["standings"].get("away", {})

        result_line = (
            f"**Result:** {r['score']} → {r['actual']} {'✅' if r['hit'] else '❌'}"
            if r["actual"] else "**Result:** Not yet played"
        )

        lines += [
            f"",
            f"### {r['tournament']}: {r['home']} vs {r['away']}",
            f"",
            f"| Signal | Home ({r['home']}) | Away ({r['away']}) |",
            f"|--------|------|------|",
            f"| Overall form (last 5) | {r['home_form']['form_str']} — {r['home_form']['points']}pts | {r['away_form']['form_str']} — {r['away_form']['points']}pts |",
        ]

        # Home/away split
        hs = r.get("home_split")
        as_ = r.get("away_split")
        if hs and as_:
            lines.append(
                f"| Venue-specific form | Home: {hs['home'].get('form_str','?')} ({hs['home'].get('points','?')}pts) "
                f"| Away: {as_['away'].get('form_str','?')} ({as_['away'].get('points','?')}pts) |"
            )

        # xG
        if xgh.get("xg_for"):
            lines += [
                f"| xG for/game | {xgh['xg_for']} (npxG {xgh.get('npxg_for','?')}) | {xga.get('xg_for','?')} (npxG {xga.get('npxg_for','?')}) |",
                f"| xG against/game | {xgh.get('xg_against','?')} | {xga.get('xg_against','?')} |",
                f"| Finishing edge | {xgh.get('finishing_edge','?'):+} | {xga.get('finishing_edge','?'):+} |",
                f"| PPDA (pressing) | {xgh.get('ppda','?')} ({xgh.get('press_intensity','?')}) | {xga.get('ppda','?')} ({xga.get('press_intensity','?')}) |",
                f"| xPTS/game | {xgh.get('xpts_per_game','?')} | {xga.get('xpts_per_game','?')} |",
            ]

        # Standings
        if sh.get("position"):
            lines.append(f"| League position | #{sh['position']} ({sh.get('points','?')}pts) | #{sa.get('position','?')} ({sa.get('points','?')}pts) |")

        # Rest
        lines.append(f"| Rest days | {r.get('home_rest_days','?')} ({r.get('home_rest_label','?')}) | {r.get('away_rest_days','?')} ({r.get('away_rest_label','?')}) |")

        # H2H
        h2h = r["h2h"]
        if h2h.get("total", 0) >= 3:
            lines.append(f"| H2H (last {h2h['total']}) | W:{h2h['home_wins']} D:{h2h['draws']} L:{h2h['away_wins']} | |")

        # Odds
        odds = r.get("odds_implied", {})
        if odds.get("p_home"):
            lines.append(
                f"| Bookmaker implied | {odds['p_home']*100:.0f}% | {odds['p_away']*100:.0f}% (draw {odds['p_draw']*100:.0f}%) |"
            )

        # Weather
        lines.append(f"| Weather | {_weather_note(r['weather'])} | |")

        # News
        nh = r["news_home"]
        na = r["news_away"]
        lines.append(f"| News sentiment | {nh.get('sentiment','?')} | {na.get('sentiment','?')} |")
        for inj in nh.get("injury_mentions", [])[:2]:
            lines.append(f"| ⚠️ Home injury news | {inj} | |")
        for inj in na.get("injury_mentions", [])[:2]:
            lines.append(f"| ⚠️ Away injury news | | {inj} |")

        lines += [
            f"",
            f"**Blended probabilities:** 1={r['p_home']*100:.0f}%  X={r['p_draw']*100:.0f}%  2={r['p_away']*100:.0f}%",
            f"",
            f"**Recommendation: {r['recommendation']}** (confidence: {r['confidence']}%)",
            f"",
            f"**Why:**{_build_reason(r)}",
            f"",
            result_line,
            f"",
            f"---",
        ]

    # Misses section
    misses = [r for r in results if r["hit"] is False]
    if misses:
        lines += [f"", f"## What We Got Wrong", f""]
        for r in misses:
            lines.append(
                f"- **{r['home']} vs {r['away']}** ({r['tournament']}): "
                f"Predicted {r['recommendation']} → Actual {r['actual']} ({r['score']})"
            )
            if r.get("odds_implied", {}).get("p_home"):
                o = r["odds_implied"]
                lines.append(
                    f"  - Odds said: home {o['p_home']*100:.0f}% / draw {o['p_draw']*100:.0f}% / away {o['p_away']*100:.0f}%"
                )

    out = OUTPUT_DIR / f"{date}.md"
    out.write_text("\n".join(lines))
    print(f"\n  Saved: {out}")
    print(f"  Accuracy: {len(hits)}/{total} = {acc}%")

    return {"date": date, "correct": len(hits), "total_finished": total, "accuracy": acc, "total_matches": len(results)}
