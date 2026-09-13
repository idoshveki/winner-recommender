"""Render the live betting record as a single self-contained HTML page.

The record lives in `v1_live_record` (picks recorded when issued, settled when
their match has a result). Reading it meant opening a SQL editor, so nobody
read it. This writes `v2/web/dashboard.html`: one file, no external assets,
openable from disk.

    python -m v2.scripts.build_dashboard              # from the live DB
    python -m v2.scripts.build_dashboard --sample     # labelled sample data
    python -m v2.scripts.build_dashboard -o /tmp/x.html

Since migration 0011 a leg can be PENDING (`hit is null`) - recorded at issue
time, not yet played. Pending is not a loss, and a slip with a pending leg is
not a resolved slip. Every count below distinguishes the three states, because
collapsing pending into "miss" is precisely the arithmetic this table exists to
prevent.

A run against an unreachable database fails loudly rather than emitting a
half-empty page.
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

SQL_LEGS = """
select system, week, leg, market, match_text, pick, odds_quoted, odds_real,
       hit, home_goals, away_goals, note, kickoff_utc
from v1_live_record
order by week desc, leg
"""

# Pre-0011 shape, so the page still builds against a database that has not had
# the migration applied yet.
SQL_LEGS_LEGACY = """
select 'v1' as system, week, leg, market, match_text, pick, odds_quoted,
       odds_real, hit, home_goals, away_goals, note,
       null::timestamptz as kickoff_utc
from v1_live_record
order by week desc, leg
"""

# 0011's view owns the rule that decides whether a slip counts at all.
SQL_WEEKS = """
select system, week, slip_legs, slip_pending, slip_won, slip_odds,
       draw_odds, draw_hit
from live_record_weeks
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
            rows = [dict(r) for r in conn.execute(SQL_LEGS)]
        except Exception as exc:
            print(f"  note: reading the pre-0011 schema ({exc})")
            rows = [dict(r) for r in conn.execute(SQL_LEGS_LEGACY)]
        if not rows:
            raise SystemExit(
                "v1_live_record is empty - nothing has been recorded yet. "
                "Run `python -m v2.ingest.v1_picks` first."
            )
        try:
            weeks = {(w["system"], w["week"]): dict(w)
                     for w in conn.execute(SQL_WEEKS)}
        except Exception as exc:
            print(f"  note: live_record_weeks unavailable ({exc}); "
                  f"resolving slips from the legs instead")
            weeks = {}
        meta = dict(conn.execute("""
            select max(m.kickoff_utc)::date as newest_match,
                   count(*) as matches_with_stats
            from matches m join match_stats s on s.match_id = m.id
        """).fetchone())
    meta["weeks_view"] = weeks
    return rows, meta


# ─────────────────────────────────────────────────────────────── sample ──

def _leg(week, leg, market, match, pick, quoted, real, hit, hg=None, ag=None,
         system="v1"):
    return dict(system=system, week=week, leg=leg, market=market,
                match_text=match, pick=pick, odds_quoted=quoted, odds_real=real,
                hit=hit, home_goals=hg, away_goals=ag, note=None, kickoff_utc=None)


def sample_rows() -> tuple[list[dict], dict]:
    """Invented data, shaped like the real thing. Always labelled as sample."""
    w = ["2026-03-07/2026-03-08", "2026-03-14/2026-03-15", "2026-03-21/2026-03-22",
         "2026-03-28/2026-03-29", "2026-04-04/2026-04-05", "2026-04-11/2026-04-12",
         "2026-04-18/2026-04-19", "2026-04-25/2026-04-26", "2026-05-02/2026-05-03"]
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

        # An issued-but-unplayed week: leg2 is pending, so the slip is not a
        # loss - it has not happened yet.
        _leg(w[8], "leg1", "H/A", "Juventus vs Verona", "H", 1.38, None, True, 2, 0),
        _leg(w[8], "leg2", "H/A", "Dortmund vs Augsburg", "H", 1.44, None, None),
        _leg(w[8], "draw", "H/A", "Sevilla vs Celta Vigo", "D", 3.70, None, None),
    ]
    return r, {"newest_match": "2026-05-03", "matches_with_stats": 8676,
               "weeks_view": {}}


# ───────────────────────────────────────────────────────────── analysis ──

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def week_date(row: dict) -> str:
    if row.get("kickoff_utc"):
        return str(row["kickoff_utc"])[:10]
    found = DATE_RE.findall(row["week"] or "")
    return found[0] if found else (row["week"] or "")


def price(row: dict):
    """(odds used, whether it is an observed price) - (None, False) if unpriced."""
    if row.get("odds_real") is not None:
        return float(row["odds_real"]), True
    if row.get("odds_quoted") is not None:
        return float(row["odds_quoted"]), False
    return None, False


def market_of(row: dict) -> str:
    m = (row.get("market") or "").strip()
    if row["leg"] == "draw":
        return "Draw singles"
    if "YC" in m.upper():
        return "YC Over 3.5"
    return m or "H/A"


def single_pnl(odds: float, hit: bool) -> float:
    # Guarded, not merely called carefully: `if hit else -1.0` would score a
    # pending leg as a full-stake loss, which is the one arithmetic error this
    # whole table exists to prevent. Callers must filter on `scored` first.
    if hit is None:
        raise ValueError("single_pnl called on a pending leg; pending is not a loss")
    return (odds - 1.0) if hit else -1.0


def binom_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def book(pnl: float, staked: int) -> dict:
    return {"pnl": pnl, "staked": staked, "roi": (pnl / staked) if staked else 0.0}


def analyse(rows: list[dict], weeks_view: dict = None) -> dict:
    weeks_view = weeks_view or {}

    for r in rows:
        r.setdefault("system", "v1")
        r["odds"], r["is_real"] = price(r)
        r["date"] = week_date(r)
        r["market_label"] = market_of(r)
        # graded: a result is known. scored: gradeable AND priced, so it can
        # carry P&L. Pending is neither, and is never a miss.
        r["graded"] = r["hit"] is not None
        r["scored"] = r["graded"] and r["odds"] is not None

    weeks: dict = {}
    for r in rows:
        key = (r["system"], r["week"])
        wk = weeks.setdefault(key, {"system": r["system"], "week": r["week"],
                                    "date": r["date"], "acc": [], "draw": None})
        if r["leg"] == "draw":
            wk["draw"] = r
        else:
            wk["acc"].append(r)
        wk["date"] = min(wk["date"], r["date"]) if wk["date"] else r["date"]

    ordered = sorted(weeks.values(), key=lambda w: (w["date"], w["week"], w["system"]),
                     reverse=True)   # newest at the top: the recent run is what matters

    slips, draws = [], []
    for wk in ordered:
        acc = wk["acc"]
        view = weeks_view.get((wk["system"], wk["week"]))
        if acc:
            # 0011's view is the authority on whether a slip counts; without it
            # we apply the same rule to the legs directly.
            row_pending = sum(1 for l in acc if not l["graded"])
            if view is not None:
                # Trust whichever source reports MORE pending. bool_and() skips
                # NULLs, so the view reports slip_won=true for a week that still
                # has an unplayed leg; only slip_pending makes that visible, and
                # disagreeing with the legs must never resolve a slip early.
                pending = max(int(view["slip_pending"] or 0), row_pending)
                resolved = pending == 0 and int(view["slip_legs"] or 0) > 0
                won = bool(view["slip_won"]) if resolved else None
            else:
                pending = row_pending
                resolved = pending == 0
                won = all(l["hit"] for l in acc) if resolved else None
            unpriced = [l for l in acc if l["odds"] is None]
            combined = None
            if not unpriced:
                combined = 1.0
                for l in acc:
                    combined *= l["odds"]
            payable = resolved and combined is not None
            wk["slip"] = {"legs": len(acc), "pending": pending, "resolved": resolved,
                          "won": won, "combined": combined, "unpriced": len(unpriced),
                          "pnl": ((combined - 1.0) if won else -1.0) if payable else None}
            if payable:
                slips.append(wk["slip"])
        else:
            wk["slip"] = None

        d = wk["draw"]
        if d is not None and d["scored"]:
            wk["draw_pnl"] = single_pnl(d["odds"], d["hit"])
            draws.append(d)
        else:
            wk["draw_pnl"] = None

        parts = [p for p in ((wk["slip"] or {}).get("pnl"), wk["draw_pnl"])
                 if p is not None]
        wk["pnl"] = sum(parts) if parts else None
        wk["pending_legs"] = sum(1 for l in acc if not l["graded"]) + \
                             (1 if d is not None and not d["graded"] else 0)

    acc_legs = [r for r in rows if r["leg"] != "draw"]
    scored_rows = [r for r in rows if r["scored"]]
    scored_acc = [r for r in acc_legs if r["scored"]]

    slips_pnl = sum(s["pnl"] for s in slips)
    draws_pnl = sum(single_pnl(d["odds"], d["hit"]) for d in draws)

    markets: dict = {}
    for r in rows:
        m = markets.setdefault(r["market_label"],
                               {"market": r["market_label"], "n": 0, "graded": 0,
                                "hits": 0, "pending": 0, "pnl": 0.0,
                                "real": r["leg"] == "draw"})
        m["n"] += 1
        if r["graded"]:
            m["graded"] += 1
            m["hits"] += 1 if r["hit"] else 0
        else:
            m["pending"] += 1
        if r["scored"]:
            m["pnl"] += single_pnl(r["odds"], r["hit"])
    market_rows = sorted(markets.values(), key=lambda m: (-m["n"], m["market"]))

    draw_hits = sum(1 for d in draws if d["hit"])
    draw_implied = (sum(1.0 / d["odds"] for d in draws) / len(draws)) if draws else 0.0
    draw_p = binom_tail(draw_hits, len(draws), draw_implied) if draws else 1.0

    systems = sorted({r["system"] for r in rows})
    by_system = []
    for sysname in systems:
        srows = [r for r in rows if r["system"] == sysname]
        sslips = [w["slip"] for w in ordered
                  if w["system"] == sysname and w["slip"] and w["slip"]["pnl"] is not None]
        sdraws = [w["draw"] for w in ordered
                  if w["system"] == sysname and w["draw"] and w["draw"]["scored"]]
        pnl = (sum(s["pnl"] for s in sslips)
               + sum(single_pnl(d["odds"], d["hit"]) for d in sdraws))
        by_system.append({"system": sysname, "legs": len(srows),
                          "pending": sum(1 for r in srows if not r["graded"]),
                          **book(pnl, len(sslips) + len(sdraws))})

    return {
        "rows": rows,
        "weeks": ordered,
        "n_legs": len(rows),
        "legs_graded": sum(1 for r in rows if r["graded"]),
        "legs_hit": sum(1 for r in rows if r["hit"]),
        "legs_pending": sum(1 for r in rows if not r["graded"]),
        "n_acc_legs": len(acc_legs),
        "n_scored_acc": len(scored_acc),
        "n_real_priced": sum(1 for r in rows if r["is_real"]),
        "n_unpriced": sum(1 for r in rows if r["odds"] is None),
        "bets": len(slips) + len(draws),
        "slips": book(slips_pnl, len(slips)),
        "slips_won": sum(1 for s in slips if s["won"]),
        "slips_lost": sum(1 for s in slips if not s["won"]),
        "slips_pending": sum(1 for w in ordered
                             if w["slip"] and not w["slip"]["resolved"]),
        "draws": book(draws_pnl, len(draws)),
        "draws_won": draw_hits,
        "draws_lost": len(draws) - draw_hits,
        "draws_pending": sum(1 for w in ordered
                             if w["draw"] is not None and not w["draw"]["graded"]),
        "draw_implied": draw_implied,
        "draw_p": draw_p,
        "acc_as_singles": book(
            sum(single_pnl(r["odds"], r["hit"]) for r in scored_acc), len(scored_acc)),
        "all_as_singles": book(
            sum(single_pnl(r["odds"], r["hit"]) for r in scored_rows), len(scored_rows)),
        "overall": book(slips_pnl + draws_pnl, len(slips) + len(draws)),
        "markets": market_rows,
        "systems": systems,
        "by_system": by_system,
        "first_date": ordered[0]["date"] if ordered else "",
        "last_date": ordered[-1]["date"] if ordered else "",
    }


# ──────────────────────────────────────────────────────────── rendering ──

MINUS = "−"


def u(x: float) -> str:
    return f"{'+' if x >= 0 else MINUS}{abs(x):.2f}u"


def pct(x: float, dp: int = 1) -> str:
    return f"{'+' if x >= 0 else MINUS}{abs(x) * 100:.{dp}f}%"


def nis(x: float) -> str:
    return f"{'+' if x >= 0 else MINUS}{abs(x) * UNIT_NIS:,.0f} NIS"


# Terms a reader should not have to already know. Rendered as dotted-underline
# tooltips rather than a glossary nobody scrolls to.
GLOSSARY = {
    "u": "unit — one bet's stake. +1u means you won one stake back as profit.",
    "slip": "accumulator: several picks on one ticket. Every leg must win or "
            "the whole ticket loses.",
    "leg": "one selection inside a slip.",
    "single": "one pick bet on its own, settled independently of any other.",
    "ROI": "return on investment: profit divided by everything staked.",
    "edge": "how much better the price is than what the model thinks it is "
            "worth. +5% means a 1.00 stake is worth 1.05.",
    "model": "the probability our model gives this outcome.",
    "book": "the probability the bookmaker's own price implies, with its "
            "margin removed.",
    "qualifies": "whether the pick cleared v2's minimum edge, i.e. whether v2 "
                 "would actually have bet it.",
    "pending": "the match has not been played or settled yet. Not a loss.",
    "v1": "the original system. Still emails picks every Friday.",
    "v2": "the rebuild. Records what it would pick but sends nothing.",
    "backtest": "computed after the fact on past matches. Never money that was "
                "actually at risk.",
}


def term(word: str, shown: str = None) -> str:
    """Wrap a jargon term in its explanation."""
    tip = GLOSSARY.get(word)
    label = shown if shown is not None else word
    if not tip:
        return e(label)
    return f'<abbr title="{e(tip)}">{e(label)}</abbr>'


def sign_class(x: float) -> str:
    return "pos" if x >= 0 else "neg"


def e(s) -> str:
    return html.escape("" if s is None else str(s))


CSS = """
:root {
  color-scheme: light;
  --page:      #f9f9f7;
  --surface:   #fcfcfb;
  --ink:       #0b0b0b;
  --ink-2:     #52514e;
  --ink-muted: #898781;
  --rule:      rgba(11, 11, 11, 0.10);
  --grid:      #e1e0d9;
  --baseline:  #c3c2b7;
  --pos:       #2a78d6;
  --neg:       #e34948;
  --good:      #006300;
  --bad:       #d03b3b;
  --meter-fill:  #2a78d6;
  --meter-track: #cde2fb;
  --flag-bg:   #fdf4e0;
  --flag-ink:  #7a4f00;
  --flag-rule: #eda100;
  --hit-bg:    #e4f5e4;
  --hit-ink:   #14591b;
  --hit-rule:  #9ed3a4;
  --miss-bg:   #fce8e8;
  --miss-ink:  #8f1f1f;
  --miss-rule: #eaa9a9;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page:      #0d0d0d;
    --surface:   #1a1a19;
    --ink:       #ffffff;
    --ink-2:     #c3c2b7;
    --ink-muted: #898781;
    --rule:      rgba(255, 255, 255, 0.10);
    --grid:      #2c2c2a;
    --baseline:  #383835;
    --pos:       #3987e5;
    --neg:       #e66767;
    --good:      #0ca30c;
    --bad:       #e66767;
    --meter-fill:  #3987e5;
    --meter-track: #184f95;
    --flag-bg:   #2a2211;
    --flag-ink:  #f0c860;
    --flag-rule: #c98500;
    --hit-bg:    #122c16;
    --hit-ink:   #7ede8b;
    --hit-rule:  #2f6b39;
    --miss-bg:   #331414;
    --miss-ink:  #f79a9a;
    --miss-rule: #7a2f2f;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page:      #0d0d0d;
  --surface:   #1a1a19;
  --ink:       #ffffff;
  --ink-2:     #c3c2b7;
  --ink-muted: #898781;
  --rule:      rgba(255, 255, 255, 0.10);
  --grid:      #2c2c2a;
  --baseline:  #383835;
  --pos:       #3987e5;
  --neg:       #e66767;
  --good:      #0ca30c;
  --bad:       #e66767;
  --meter-fill:  #3987e5;
  --meter-track: #184f95;
  --flag-bg:   #2a2211;
  --flag-ink:  #f0c860;
  --flag-rule: #c98500;
  --hit-bg:    #122c16;
  --hit-ink:   #7ede8b;
  --hit-rule:  #2f6b39;
  --miss-bg:   #331414;
  --miss-ink:  #f79a9a;
  --miss-rule: #7a2f2f;
}

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--page);
  color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 16px;
  line-height: 1.55;
}
.wrap { max-width: 64rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }

h1 { font-size: 1.5rem; font-weight: 600; letter-spacing: -0.01em; margin: 0 0 .35rem; }
h2 {
  font-size: .8125rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: .07em; color: var(--ink-2);
  margin: 3rem 0 .9rem; padding-bottom: .5rem; border-bottom: 1px solid var(--rule);
}
p { margin: 0 0 .85rem; }
.sub { color: var(--ink-2); font-size: .875rem; margin: 0; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: .9em; font-variant-ligatures: none; }
.muted { color: var(--ink-muted); font-size: .8125rem; }
.note { color: var(--ink-2); font-size: .875rem; max-width: 46rem; }

.flag {
  background: var(--flag-bg); color: var(--flag-ink);
  border: 1px solid var(--flag-rule); border-left-width: 4px;
  border-radius: 4px; padding: .75rem 1rem; margin: 1.25rem 0 0;
  font-size: .875rem;
}
.flag strong { letter-spacing: .04em; }

.card {
  background: var(--surface); border: 1px solid var(--rule);
  border-radius: 6px; padding: 1.1rem 1.2rem;
}

.hero { margin: 1.75rem 0 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: .35rem 1.25rem; }
.hero .figure { font-size: 3.25rem; font-weight: 600; line-height: 1; letter-spacing: -0.02em; }
.hero .figure.pos { color: var(--good); }
.hero .figure.neg { color: var(--bad); }
.hero .beside { color: var(--ink-2); font-size: 1rem; }
.hero .label { width: 100%; font-size: .8125rem; color: var(--ink-muted);
  text-transform: uppercase; letter-spacing: .07em; margin-bottom: .5rem; }

.tiles { display: grid; gap: .75rem; margin-top: 1.5rem;
  grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); }
.tile .label { font-size: .8125rem; color: var(--ink-2); margin-bottom: .3rem; }
.tile .value { font-size: 1.65rem; font-weight: 600; line-height: 1.1; letter-spacing: -0.01em; }
.tile .value.pos { color: var(--good); }
.tile .value.neg { color: var(--bad); }
.tile .foot { font-size: .8125rem; color: var(--ink-muted); margin-top: .3rem; }

/* Value in its own column rather than floating at the bar tip: a tip label
   overflows the track (and the page with it) as soon as a bar runs long. */
.cmp { margin: .25rem 0 0; }
.cmp-row, .cmp-axis {
  display: grid; grid-template-columns: minmax(9rem, 16rem) 1fr 4.25rem;
  column-gap: .75rem; align-items: center;
}
.cmp-label { padding: .5rem 0; font-size: .875rem; }
.cmp-label .cmp-sub { display: block; color: var(--ink-muted); font-size: .75rem; }
.cmp-track { position: relative; min-height: 2.75rem; }
.cmp-zero { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--baseline); }
.cmp-bar { position: absolute; top: 50%; transform: translateY(-50%); height: 20px; }
.cmp-bar.pos { background: var(--pos); border-radius: 0 4px 4px 0; }
.cmp-bar.neg { background: var(--neg); border-radius: 4px 0 0 4px; }
.cmp-val { font-size: .875rem; font-weight: 600; color: var(--ink);
  text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.cmp-axis { font-size: .75rem; color: var(--ink-muted); }
.cmp-axis .tick { position: relative; height: 1.25rem; }
.cmp-axis .tick span { position: absolute; transform: translateX(-50%); }
@media (max-width: 36rem) {
  .cmp-row, .cmp-axis { grid-template-columns: 1fr 4.25rem; }
  .cmp-label { grid-column: 1 / -1; padding-bottom: 0; }
}

.scroll { overflow-x: auto; border: 1px solid var(--rule); border-radius: 6px;
  background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: .875rem; }
th, td { text-align: left; padding: .6rem .85rem; vertical-align: top;
  border-bottom: 1px solid var(--rule); }
thead th { font-size: .75rem; text-transform: uppercase; letter-spacing: .06em;
  color: var(--ink-muted); font-weight: 600; white-space: nowrap; }
tbody tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td.pos { color: var(--good); } td.neg { color: var(--bad); }
td.date { white-space: nowrap; font-variant-numeric: tabular-nums; color: var(--ink-2); }

.meter { display: inline-block; vertical-align: middle; width: 5.5rem; height: 6px;
  background: var(--meter-track); border-radius: 3px; overflow: hidden; margin-right: .5rem; }
.meter > i { display: block; height: 100%; background: var(--meter-fill); border-radius: 3px; }

.leg { padding: .15rem 0; }
.leg + .leg { border-top: 1px dashed var(--rule); margin-top: .35rem; padding-top: .5rem; }
.leg-match { display: block; white-space: nowrap; }
.leg-line { display: flex; align-items: baseline; gap: .5rem; white-space: nowrap; }
.leg-meta { color: var(--ink-muted); font-size: .8125rem; white-space: nowrap;
  font-variant-numeric: tabular-nums; }
.chip { font-size: .6875rem; font-weight: 700; letter-spacing: .06em;
  border-radius: 3px; padding: .1rem .4rem; white-space: nowrap;
  border: 1px solid transparent; }
.chip.hit     { color: var(--hit-ink);  background: var(--hit-bg);  border-color: var(--hit-rule); }
.chip.miss    { color: var(--miss-ink); background: var(--miss-bg); border-color: var(--miss-rule); }
.chip.pending { color: var(--ink-muted); background: var(--rule); }
/* whole-row tint so a good or bad week reads at a glance rather than needing
   the eye to find a small coloured word */
tr.row-hit  > td { background: var(--hit-bg); }
tr.row-miss > td { background: var(--miss-bg); }
tr.row-pending > td { background: transparent; }

/* plain-language tooltips: several terms here are jargon (u, slip, edge) and
   the page should not require the reader to already know them */
abbr[title] { text-decoration: underline dotted; text-underline-offset: 2px;
  cursor: help; border: none; }
.sys { font-size: .6875rem; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: var(--ink-2); border: 1px solid var(--rule);
  border-radius: 3px; padding: .05rem .35rem; white-space: nowrap; }
sup.src { font-size: .625rem; color: var(--ink-muted); margin-left: .1rem; }

footer { margin-top: 3rem; padding-top: 1.25rem; border-top: 1px solid var(--rule);
  color: var(--ink-2); font-size: .875rem; }
footer ul { margin: .5rem 0 0; padding-left: 1.1rem; }
footer li { margin-bottom: .45rem; }
"""


def comparison_chart(items: list) -> str:
    """items: (label, sublabel, roi as fraction, hover title)."""
    vals = [i[2] for i in items]
    vmax, vmin = max(0.0, max(vals)), min(0.0, min(vals))
    span = (vmax - vmin) or 1.0
    pad = span * 0.14
    dmax, dmin = vmax + pad, vmin - pad
    width = dmax - dmin
    zero = (0.0 - dmin) / width * 100.0

    out = ['<div class="cmp">']
    for label, sub, v, title in items:
        w = abs(v) / width * 100.0
        left = zero if v >= 0 else zero - w
        cls = "pos" if v >= 0 else "neg"
        out.append(
            f'<div class="cmp-row">'
            f'<div class="cmp-label">{e(label)}<span class="cmp-sub">{e(sub)}</span></div>'
            f'<div class="cmp-track">'
            f'<div class="cmp-zero" style="left:{zero:.3f}%"></div>'
            f'<div class="cmp-bar {cls}" style="left:{left:.3f}%;width:{w:.3f}%" '
            f'title="{e(title)}"></div></div>'
            f'<div class="cmp-val">{pct(v)}</div></div>'
        )
    out.append(
        f'<div class="cmp-axis"><div></div>'
        f'<div class="tick"><span style="left:{zero:.3f}%">0%</span></div><div></div></div>'
    )
    out.append("</div>")
    return "".join(out)


def leg_html(r: dict, *, show_market: bool = True) -> str:
    if r["odds"] is None:
        oddstr = "no price recorded"
    else:
        src = ("<sup class=\"src\" title=\"observed price\">obs</sup>" if r["is_real"]
               else "<sup class=\"src\" title=\"price as the system quoted it\">q</sup>")
        oddstr = f'@{r["odds"]:.2f}{src}'
    score = ""
    if r.get("home_goals") is not None and r.get("away_goals") is not None:
        score = f" &middot; {r['home_goals']}–{r['away_goals']}"
    if not r["graded"]:
        chip = '<span class="chip pending">PENDING</span>'
    elif r["hit"]:
        chip = '<span class="chip hit">HIT</span>'
    else:
        chip = '<span class="chip miss">MISS</span>'
    bits = [e(r["market_label"])] if show_market else []
    if r.get("pick"):
        bits.append(e(r["pick"]))
    bits.append(oddstr)
    meta = " &middot; ".join(bits) + score
    return (f'<div class="leg"><span class="leg-match">{e(r["match_text"])}</span>'
            f'<span class="leg-line"><span class="leg-meta">{meta}</span>{chip}</span></div>')


GLOSSARY = {
    "u": "One unit \u2014 the stake on a single bet. +1u means you won one stake in profit.",
    "slip": "Accumulator. Several picks on one ticket \u2014 every leg must win or the whole ticket loses.",
    "leg": "One selection inside a slip.",
    "single": "A pick bet on its own, settled independently of anything else.",
    "ROI": "Profit divided by everything staked.",
    "pending": "Not played or not settled yet. Not a loss.",
    "v1": "The original system. Still emails picks every Friday.",
    "v2": "The rebuild. Records what it would pick, sends nothing.",
}


def tip(key, shown=None):
    """Tap-or-hover tooltip. <abbr title> needs a hover, which phones do not
    have, so this is a button with a popover instead."""
    txt = GLOSSARY.get(key)
    label = e(shown if shown is not None else key)
    if not txt:
        return label
    return (f'<button class="t" type="button" aria-label="{e(txt)}">{label}'
            f'<span class="tt" role="tooltip">{e(txt)}</span></button>')


def render(a, meta, *, sample=False):
    W = a["weeks"]
    ov, sl, dr = a["overall"], a["slips"], a["draws"]

    def cls(x):
        return "pos" if x > 0 else ("neg" if x < 0 else "")

    # ---- week rows, newest first (already sorted that way) ----------------
    rows_html = []
    for w in W:
        legs = []
        for l in w["acc"] + ([w["draw"]] if w["draw"] else []):
            st = "pending" if l["hit"] is None else ("hit" if l["hit"] else "miss")
            legs.append(
                f'<div class="lg {st}"><span class="m">{e(l["match_text"])}</span>'
                f'<span class="p">{e(l["pick"])} @ {l["odds"]:.2f}</span></div>')
        pnl = w["pnl"]
        if w["draw_pnl"] is not None:
            pnl = (pnl or 0) + w["draw_pnl"]
        badge = ("" if pnl is None
                 else f'<span class="amt {cls(pnl)}">{pnl:+.2f}u</span>')
        rows_html.append(
            f'<article class="wk">'
            f'<header><time>{e(w["date"])}</time>'
            f'<span class="sys">{e(w["system"])}</span>{badge}</header>'
            f'{"".join(legs)}</article>')

    mkt = "".join(
        f'<tr><td>{e(m["market"])}</td>'
        f'<td class="n">{m["hits"]}/{m["graded"]}</td>'
        f'<td class="n">{(m["hits"]/m["graded"]*100 if m["graded"] else 0):.0f}%</td>'
        f'<td class="n {cls(m["pnl"])}">{m["pnl"]:+.2f}</td></tr>'
        for m in a["markets"])

    single, slips = a["all_as_singles"], a["slips"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Betting record</title>
<style>
:root{{color-scheme:light;--bg:#faf9f7;--card:#fff;--ink:#111;--dim:#6b6a66;
--line:rgba(0,0,0,.11);--good:#0f7a2e;--bad:#c62f2f;
--hit:#e6f5e8;--hitl:#a8d8b2;--miss:#fdeaea;--missl:#eaadad;--tipbg:#1c1c1a}}
@media(prefers-color-scheme:dark){{:root:not([data-theme=light]){{color-scheme:dark;
--bg:#0e0e0d;--card:#1a1a18;--ink:#f5f4f1;--dim:#9b9a95;--line:rgba(255,255,255,.13);
--good:#4ec36b;--bad:#f07070;--hit:#14301a;--hitl:#2f6b3c;--miss:#331515;--missl:#7a3030;
--tipbg:#3a3a37}}}}
:root[data-theme=dark]{{color-scheme:dark;--bg:#0e0e0d;--card:#1a1a18;--ink:#f5f4f1;
--dim:#9b9a95;--line:rgba(255,255,255,.13);--good:#4ec36b;--bad:#f07070;
--hit:#14301a;--hitl:#2f6b3c;--miss:#331515;--missl:#7a3030;--tipbg:#3a3a37}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
-webkit-text-size-adjust:100%}}
.wrap{{max-width:46rem;margin:0 auto;padding:1.5rem 1rem 3rem}}
h1{{font-size:1.05rem;font-weight:600;margin:0}}
.dim{{color:var(--dim);font-size:.82rem}}
h2{{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
margin:2.2rem 0 .6rem;font-weight:600}}
.big{{font-size:2.9rem;font-weight:650;letter-spacing:-.02em;line-height:1.05;margin:.6rem 0 .1rem}}
.big.pos{{color:var(--good)}}.big.neg{{color:var(--bad)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(8.5rem,1fr));gap:.6rem;margin-top:1.2rem}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.7rem .8rem}}
.tile b{{display:block;font-size:1.35rem;font-weight:650;line-height:1.2}}
.tile span{{font-size:.75rem;color:var(--dim)}}
table{{width:100%;border-collapse:collapse;font-size:.87rem;background:var(--card);
border:1px solid var(--line);border-radius:8px;overflow:hidden}}
th,td{{padding:.5rem .7rem;text-align:left;border-bottom:1px solid var(--line)}}
th{{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);font-weight:600}}
tr:last-child td{{border-bottom:none}}
.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.pos{{color:var(--good)}}.neg{{color:var(--bad)}}
.wk{{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:.6rem .75rem;margin-bottom:.5rem}}
.wk header{{display:flex;align-items:center;gap:.5rem;margin-bottom:.45rem}}
.wk time{{font-variant-numeric:tabular-nums;font-size:.82rem;color:var(--dim)}}
.sys{{font-size:.65rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
border:1px solid var(--line);border-radius:4px;padding:.05rem .3rem;color:var(--dim)}}
.amt{{margin-left:auto;font-weight:650;font-variant-numeric:tabular-nums;font-size:.9rem}}
.lg{{display:flex;flex-wrap:wrap;gap:.15rem .6rem;padding:.35rem .5rem;border-radius:5px;
margin-top:.25rem;border:1px solid transparent}}
.lg.hit{{background:var(--hit);border-color:var(--hitl)}}
.lg.miss{{background:var(--miss);border-color:var(--missl)}}
.lg.pending{{background:transparent;border-style:dashed;border-color:var(--line)}}
.lg .m{{font-size:.87rem}}
.lg .p{{margin-left:auto;font-size:.8rem;color:var(--dim);
font-variant-numeric:tabular-nums;white-space:nowrap}}
.t{{all:unset;cursor:pointer;position:relative;border-bottom:1px dotted currentColor}}
.t .tt{{position:absolute;left:0;top:calc(100% + .4rem);z-index:9;width:min(15rem,72vw);
background:var(--tipbg);color:#fff;font-size:.78rem;line-height:1.4;font-weight:400;
text-transform:none;letter-spacing:0;padding:.5rem .6rem;border-radius:6px;
opacity:0;visibility:hidden;transition:opacity .12s;pointer-events:none;
box-shadow:0 4px 14px rgba(0,0,0,.28)}}
.t:hover .tt,.t:focus .tt,.t.on .tt{{opacity:1;visibility:visible}}
footer{{margin-top:2.4rem;padding-top:1rem;border-top:1px solid var(--line);
color:var(--dim);font-size:.8rem}}
@media(max-width:30rem){{.big{{font-size:2.3rem}}.lg .p{{margin-left:0;width:100%}}}}
</style></head><body><div class="wrap">

<h1>Betting record</h1>
<p class="dim">{e(a["first_date"])} to {e(a["last_date"])} &middot;
{a["bets"]} bets &middot; {tip("v1")} live, {tip("v2")} recording only</p>

<div class="big {cls(ov['pnl'])}">{ov['pnl']:+.2f}u</div>
<p class="dim">{nis(ov['pnl'])} at {UNIT_NIS} per {tip("u","unit")} &middot;
{pct(ov['roi'])} {tip("ROI")}</p>

<div class="tiles">
  <div class="tile"><b>{a['slips_won']}&ndash;{a['slips_lost']}</b>
    <span>{tip("slip","slips")} &middot; <span class="{cls(sl['pnl'])}">{sl['pnl']:+.2f}u</span></span></div>
  <div class="tile"><b>{a['draws_won']}&ndash;{a['draws_lost']}</b>
    <span>draw {tip("single","singles")} &middot; <span class="{cls(dr['pnl'])}">{dr['pnl']:+.2f}u</span></span></div>
  <div class="tile"><b>{a['legs_hit']}/{a['legs_graded']}</b>
    <span>{tip("leg","legs")} won{f" &middot; {a['legs_pending']} {tip('pending')}" if a['legs_pending'] else ""}</span></div>
</div>

<h2>Singles beat slips</h2>
<table><tbody>
<tr><td>Every pick as its own bet</td>
  <td class="n {cls(single['pnl'])}">{single['pnl']:+.2f}u</td>
  <td class="n {cls(single['roi'])}">{pct(single['roi'],0)}</td></tr>
<tr><td>The same picks bundled into {tip("slip","slips")}</td>
  <td class="n {cls(slips['pnl'])}">{slips['pnl']:+.2f}u</td>
  <td class="n {cls(slips['roi'])}">{pct(slips['roi'],0)}</td></tr>
</tbody></table>
<p class="dim" style="margin-top:.5rem">Every extra leg multiplies the bookmaker's
margin against you and needs everything to land.</p>

<h2>By market</h2>
<table><thead><tr><th>Market</th><th class="n">Won</th><th class="n">Rate</th>
<th class="n">P&amp;L</th></tr></thead><tbody>{mkt}</tbody></table>

<h2>Week by week &middot; newest first</h2>
{"".join(rows_html)}

<footer>
{a['draws_won']} of {a['draws_won']+a['draws_lost']} draw singles landed at a
market-implied {pct(a['draw_implied'],0)} each &mdash; about a 1-in-{int(1/max(a['draw_p'],1e-9))}
run. On {a['draws_won']+a['draws_lost']} bets that is a hot streak, not proof.
Card legs are priced at what 1win actually pays, not the 1.50 v1 assumed.
{'<br><strong>Sample data &mdash; not real results.</strong>' if sample else ''}
</footer>
</div>
<script>
// tap to toggle on touch devices, where :hover never fires
document.querySelectorAll('.t').forEach(function(b){{
  b.addEventListener('click', function(ev){{
    ev.stopPropagation();
    var open = b.classList.contains('on');
    document.querySelectorAll('.t.on').forEach(function(o){{o.classList.remove('on');}});
    if (!open) b.classList.add('on');
  }});
}});
document.addEventListener('click', function(){{
  document.querySelectorAll('.t.on').forEach(function(o){{o.classList.remove('on');}});
}});
</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--sample", action="store_true",
                    help="render invented, clearly-labelled data instead of the DB")
    args = ap.parse_args()

    rows, meta = sample_rows() if args.sample else load_from_db()
    page = render(analyse(rows, meta.get("weeks_view")), meta, sample=args.sample)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    kind = "SAMPLE" if args.sample else "live"
    print(f"  wrote {args.out} ({len(page):,} bytes, {kind} data)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
