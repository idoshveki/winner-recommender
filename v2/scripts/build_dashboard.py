"""Render the live betting record as a single self-contained HTML page.

The record lives in `v1_live_record` (v1's recommendations, graded on v2's
data). Reading it meant opening a SQL editor, so nobody read it. This writes
`v2/web/dashboard.html`: one file, no external assets, openable from disk.

    python -m v2.scripts.build_dashboard              # from the live DB
    python -m v2.scripts.build_dashboard --sample     # labelled sample data
    python -m v2.scripts.build_dashboard -o /tmp/x.html

A run against an unreachable database fails loudly rather than emitting a
half-empty page - the failure mode this whole project exists to avoid.
"""
from __future__ import annotations

import argparse
import html
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from v2.lib.config import V2

UNIT_NIS = 50
OUT_DEFAULT = V2 / "web" / "dashboard.html"

# ────────────────────────────────────────────────────────────── loading ──

# v1_live_record carries the graded legs. kickoff_utc is a nicety, resolved
# through team_aliases the same way settle_v1_picks resolved it; if anything
# about that join fails we still render, just dated from the week label.
SQL_ENRICHED = """
select r.week, r.leg, r.market, r.match_text, r.pick,
       r.odds_quoted, r.odds_real, r.hit, r.home_goals, r.away_goals, r.note,
       mm.kickoff_utc
from v1_live_record r
left join lateral (
    select m.kickoff_utc
    from matches m
    join match_stats s on s.match_id = m.id
    where m.home_team_id = (
            select coalesce(
              (select team_id from team_aliases
                where lower(alias) = lower(btrim(split_part(r.match_text, ' vs ', 1))) limit 1),
              (select id from teams
                where lower(canonical_name) = lower(btrim(split_part(r.match_text, ' vs ', 1))) limit 1)))
      and m.away_team_id = (
            select coalesce(
              (select team_id from team_aliases
                where lower(alias) = lower(btrim(split_part(r.match_text, ' vs ', 2))) limit 1),
              (select id from teams
                where lower(canonical_name) = lower(btrim(split_part(r.match_text, ' vs ', 2))) limit 1)))
    order by m.kickoff_utc
    limit 1
) mm on true
order by r.week, r.leg
"""

SQL_PLAIN = """
select week, leg, market, match_text, pick, odds_quoted, odds_real, hit,
       home_goals, away_goals, note, null::timestamptz as kickoff_utc
from v1_live_record order by week, leg
"""


def load_from_db() -> tuple[list[dict], dict]:
    """Read the record. Raises with a readable message if the DB is down."""
    from psycopg.rows import dict_row

    from v2.lib.jobs import connect

    try:
        conn = connect()
    except Exception as exc:
        raise SystemExit(
            f"cannot reach the database, so there is nothing to render.\n"
            f"  {type(exc).__name__}: {str(exc).splitlines()[0]}\n"
            f"  Check DATABASE_URL in .env, and that the Supabase project is not paused.\n"
            f"  To preview the layout instead, run with --sample."
        ) from exc

    with conn:
        conn.autocommit = True
        conn.row_factory = dict_row
        try:
            rows = [dict(r) for r in conn.execute(SQL_ENRICHED)]
        except Exception as exc:
            print(f"  note: kickoff enrichment failed ({exc}); dating from week labels")
            rows = [dict(r) for r in conn.execute(SQL_PLAIN)]
        if not rows:
            raise SystemExit(
                "v1_live_record is empty - nothing has been settled yet. "
                "Run v2/scripts/settle_v1_picks.py first."
            )
        meta = dict(conn.execute("""
            select max(m.kickoff_utc)::date as newest_match,
                   count(*) as matches_with_stats
            from matches m join match_stats s on s.match_id = m.id
        """).fetchone())
    return rows, meta


# ─────────────────────────────────────────────────────────────── sample ──

def _leg(week, leg, market, match, pick, quoted, real, hit, hg=None, ag=None):
    return dict(week=week, leg=leg, market=market, match_text=match, pick=pick,
                odds_quoted=quoted, odds_real=real, hit=hit,
                home_goals=hg, away_goals=ag, note=None, kickoff_utc=None)


def sample_rows() -> tuple[list[dict], dict]:
    """Invented data, shaped like the real thing. Always labelled as sample."""
    w = ["2026-03-07/2026-03-08", "2026-03-14/2026-03-15", "2026-03-21/2026-03-22",
         "2026-03-28/2026-03-29", "2026-04-04/2026-04-05", "2026-04-11/2026-04-12",
         "2026-04-18/2026-04-19", "2026-04-25/2026-04-26"]
    r = [
        _leg(w[0], "leg1", "H/A", "Inter vs Monza", "H", 1.19, None, True, 2, 0),
        _leg(w[0], "leg2", "H/A", "Real Madrid vs Alaves", "H", 1.30, None, True, 3, 1),
        _leg(w[0], "draw", "H/A", "Torino vs Udinese", "D", 3.75, None, True, 1, 1),

        _leg(w[1], "leg1", "H/A", "Bayern Munich vs Heidenheim", "H", 1.19, None, True, 4, 0),
        _leg(w[1], "leg2", "H/A", "Man City vs Ipswich", "H", 1.35, None, True, 2, 1),
        _leg(w[1], "draw", "H/A", "Getafe vs Osasuna", "D", 3.60, None, True, 0, 0),

        _leg(w[2], "leg1", "H/A", "Liverpool vs Southampton", "H", 1.25, None, True, 3, 0),
        _leg(w[2], "leg2", "H/A", "Napoli vs Lecce", "H", 1.28, None, True, 2, 1),
        _leg(w[2], "draw", "H/A", "Wolfsburg vs Mainz", "D", 3.95, None, True, 2, 2),

        _leg(w[3], "leg1", "YC Over 3.5", "Atletico Madrid vs Valencia", "Over", 1.50, 1.42, True, 1, 1),
        _leg(w[3], "leg2", "H/A", "Arsenal vs Brentford", "H", 1.40, None, False, 1, 2),
        _leg(w[3], "draw", "H/A", "Empoli vs Cagliari", "D", 4.15, None, True, 1, 1),

        _leg(w[4], "leg1", "YC Over 3.5", "Roma vs Lazio", "Over", 1.50, 1.42, True, 2, 2),
        _leg(w[4], "leg2", "H/A", "Barcelona vs Girona", "H", 1.32, None, False, 1, 1),
        _leg(w[4], "draw", "H/A", "Brentford vs Crystal Palace", "D", 3.90, None, False, 2, 0),

        _leg(w[5], "draw", "H/A", "Union Berlin vs Hoffenheim", "D", 3.85, None, True, 1, 1),
        _leg(w[6], "draw", "H/A", "Bologna vs Torino", "D", 3.65, None, True, 0, 0),
        _leg(w[7], "draw", "H/A", "Wolves vs Everton", "D", 3.80, None, False, 0, 1),
    ]
    return r, {"newest_match": "2026-04-26", "matches_with_stats": 8676}


# ───────────────────────────────────────────────────────────── analysis ──

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def week_date(row: dict) -> str:
    if row.get("kickoff_utc"):
        return str(row["kickoff_utc"])[:10]
    found = DATE_RE.findall(row["week"] or "")
    return found[-1] if found else (row["week"] or "")


def price(row: dict) -> tuple[float, bool]:
    """(odds used, whether it is an observed price rather than v1's quote)."""
    if row.get("odds_real") is not None:
        return float(row["odds_real"]), True
    if row.get("odds_quoted") is not None:
        return float(row["odds_quoted"]), False
    raise SystemExit(f"leg with no price at all: {row['week']} {row['leg']}")


def market_of(row: dict) -> str:
    m = (row.get("market") or "").strip()
    if row["leg"] == "draw":
        return "Draw singles"
    if "YC" in m.upper():
        return "YC Over 3.5"
    return m or "H/A"


def single_pnl(odds: float, hit: bool) -> float:
    return (odds - 1.0) if hit else -1.0


def binom_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def analyse(rows: list[dict]) -> dict:
    for r in rows:
        r["odds"], r["is_real"] = price(r)
        r["date"] = week_date(r)
        r["market_label"] = market_of(r)

    weeks: dict[str, dict] = {}
    for r in rows:
        wk = weeks.setdefault(r["week"], {"week": r["week"], "date": r["date"],
                                          "acc": [], "draw": None})
        if r["leg"] == "draw":
            wk["draw"] = r
        else:
            wk["acc"].append(r)
        wk["date"] = min(wk["date"], r["date"]) if wk["date"] else r["date"]

    ordered = sorted(weeks.values(), key=lambda w: (w["date"], w["week"]))

    slips, draws = [], []
    for wk in ordered:
        if wk["acc"]:
            combined = 1.0
            for leg in wk["acc"]:
                combined *= leg["odds"]
            won = all(leg["hit"] for leg in wk["acc"])
            wk["slip"] = {"combined": combined, "won": won,
                          "pnl": (combined - 1.0) if won else -1.0}
            slips.append(wk["slip"])
        else:
            wk["slip"] = None
        if wk["draw"]:
            d = wk["draw"]
            wk["draw_pnl"] = single_pnl(d["odds"], d["hit"])
            draws.append(d)
        else:
            wk["draw_pnl"] = None
        wk["pnl"] = (wk["slip"]["pnl"] if wk["slip"] else 0.0) + (wk["draw_pnl"] or 0.0)

    acc_legs = [r for r in rows if r["leg"] != "draw"]

    def book(pnl: float, staked: int) -> dict:
        return {"pnl": pnl, "staked": staked,
                "roi": (pnl / staked) if staked else 0.0}

    slips_pnl = sum(s["pnl"] for s in slips)
    draws_pnl = sum(single_pnl(d["odds"], d["hit"]) for d in draws)
    acc_single_pnl = sum(single_pnl(r["odds"], r["hit"]) for r in acc_legs)
    all_single_pnl = sum(single_pnl(r["odds"], r["hit"]) for r in rows)

    markets: dict[str, dict] = {}
    for r in rows:
        m = markets.setdefault(r["market_label"],
                               {"market": r["market_label"], "n": 0, "hits": 0, "pnl": 0.0,
                                "real": r["leg"] == "draw"})
        m["n"] += 1
        m["hits"] += 1 if r["hit"] else 0
        m["pnl"] += single_pnl(r["odds"], r["hit"])
    market_rows = sorted(markets.values(), key=lambda m: (-m["n"], m["market"]))

    draw_hits = sum(1 for d in draws if d["hit"])
    draw_implied = (sum(1.0 / d["odds"] for d in draws) / len(draws)) if draws else 0.0
    draw_p = binom_tail(draw_hits, len(draws), draw_implied) if draws else 1.0

    return {
        "rows": rows,
        "weeks": ordered,
        "n_legs": len(rows),
        "n_acc_legs": len(acc_legs),
        "n_real_priced": sum(1 for r in rows if r["is_real"]),
        "bets": len(slips) + len(draws),
        "slips": book(slips_pnl, len(slips)),
        "slips_won": sum(1 for s in slips if s["won"]),
        "slips_lost": sum(1 for s in slips if not s["won"]),
        "draws": book(draws_pnl, len(draws)),
        "draws_won": draw_hits,
        "draws_lost": len(draws) - draw_hits,
        "draw_implied": draw_implied,
        "draw_p": draw_p,
        "acc_as_singles": book(acc_single_pnl, len(acc_legs)),
        "all_as_singles": book(all_single_pnl, len(rows)),
        "overall": book(slips_pnl + draws_pnl, len(slips) + len(draws)),
        "markets": market_rows,
        "first_date": ordered[0]["date"] if ordered else "",
        "last_date": ordered[-1]["date"] if ordered else "",
    }
