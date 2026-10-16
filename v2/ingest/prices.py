"""Fetch cards/corners ladders for upcoming fixtures and fit implied distributions.

Credits are markets x regions per event, so a run is budgeted and refuses to
start if it would exceed the cap. v1 had no accounting at all and simply
stopped working when a quota ran out.

Snapshots are written change-only. v1 re-inserted identical prices daily
forever, which is how odds_raw became 98% of a 55 MB database.

    python -m v2.ingest.prices [--days 8] [--dry-run]
"""
from __future__ import annotations

import argparse
from typing import Dict, List

from v2.ingest import odds_api
from v2.lib.config import (
    CARD_LINES, CORNER_LINES, MARKET_CARDS, MARKET_CORNERS,
    ODDS_API_REGIONS, SHARP_BOOKS,
)
from v2.lib.jobs import connect, job
from v2.model.devig import fit_ladder, ladder_from_outcomes, overround

ODDS_API_MARKET_KEYS = ["alternate_totals_cards", "alternate_totals_corners", "h2h"]
MONTHLY_CAP = 15000          # 75% of the 20k plan, leaving headroom


def pending_matches(conn, days: int) -> List[tuple]:
    return conn.execute(
        """select m.id, m.odds_api_event_id, m.league, m.kickoff_utc,
                  th.canonical_name, ta.canonical_name
           from matches m
           join teams th on th.id = m.home_team_id
           join teams ta on ta.id = m.away_team_id
           where m.odds_api_event_id is not null
             and m.status = 'scheduled'
             and m.kickoff_utc between now() and now() + (%s || ' days')::interval
           order by m.kickoff_utc""",
        (days,),
    ).fetchall()


def month_to_date_spend(conn) -> int:
    row = conn.execute(
        """select coalesce(sum((meta->>'credits_used')::int), 0)
           from job_runs
           where job = 'fetch-prices'
             and started_at >= date_trunc('month', now())"""
    ).fetchone()
    return row[0] or 0


def latest_prices(conn, match_id: int) -> Dict[tuple, float]:
    """Most recent stored price for every (book, market, side, line) of a match.

    One query for the whole match. The previous version asked the database once
    per outcome, which with ~41 bookmakers across three markets came to roughly
    33,000 round trips per run; at the 66 ms latency to the pooler that is 36
    minutes of waiting, and the job has a 20-minute timeout. It was cancelled,
    not failed, so it did not even report as a failure.

    This is the same mistake as the original Tokyo-region load that took four
    hours before being rewritten with COPY. Latency times round trips is the
    cost model for this database; the number of round trips is the thing to cut.
    """
    rows = conn.execute(
        """select bookmaker, market, side, line, price from (
               select bookmaker, market, side, line, price,
                      row_number() over (
                          partition by bookmaker, market, side, line
                          order by fetched_at desc) as rn
                 from odds_snapshots
                where match_id = %s) t
            where rn = 1""", (match_id,)).fetchall()
    return {(b, m, sd, float(ln) if ln is not None else None): float(pr)
            for b, m, sd, ln, pr in rows}


def changed_snapshots(match_id: int, book: str, market: str, outcomes,
                      known: Dict[tuple, float]) -> List[tuple]:
    """Rows worth inserting: those whose price differs from the stored one.

    Updates `known` as it goes, so two bookmakers quoting the same line in one
    payload cannot both be written as changes.
    """
    out = []
    for o in outcomes:
        # h2h has no point/line; totals markets do. Skipping null points here
        # silently discarded every 1X2 price.
        point = o.get("point")
        line = float(point) if point is not None else None
        side, price = o["name"], float(o["price"])
        key = (book, market, side, line)
        if known.get(key) == price:
            continue
        known[key] = price
        out.append((match_id, book, market, line, side, price))
    return out


def fit_and_store(conn, match_id: int, book: str, market: str, outcomes) -> bool:
    pts = ladder_from_outcomes(outcomes, method="shin")
    if len(pts) < 2:
        return False
    fit = fit_ladder(pts)
    # mean overround across two-sided lines, for margin monitoring
    by_line: Dict[float, Dict[str, float]] = {}
    for o in outcomes:
        if o.get("point") is not None:
            by_line.setdefault(float(o["point"]), {})[o["name"].lower()] = float(o["price"])
    ors = [overround(v["over"], v["under"])
           for v in by_line.values() if "over" in v and "under" in v]
    conn.execute(
        """insert into market_implied (match_id, bookmaker, market, n_lines,
                                       overround, devig_method, implied_mean,
                                       dispersion, fit_rmse)
           values (%s,%s,%s,%s,%s,'shin',%s,%s,%s)
           on conflict do nothing""",
        (match_id, book, market, fit.n_lines,
         sum(ors) / len(ors) if ors else None,
         fit.mu, fit.dispersion, fit.rmse),
    )
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with connect() as conn:
        matches = pending_matches(conn, args.days)
        spent = month_to_date_spend(conn)
    cost = odds_api.planned_cost(len(matches), ODDS_API_MARKET_KEYS, ODDS_API_REGIONS)
    print(f"  {len(matches)} fixtures, planned cost {cost} credits "
          f"(month to date {spent}, cap {MONTHLY_CAP})")
    if spent + cost > MONTHLY_CAP:
        raise SystemExit(
            f"refusing to run: {spent} + {cost} would exceed the {MONTHLY_CAP} cap"
        )
    if args.dry_run:
        print("  dry run, nothing fetched")
        return 0

    with job("fetch-prices") as r, connect() as conn:
        conn.autocommit = False
        fits = done = 0
        for match_id, event_id, league, ko, home, away in matches:
            sport = odds_api.sport_key(league)
            try:
                data = odds_api.event_odds(sport, event_id, ODDS_API_MARKET_KEYS)
            except Exception as exc:
                print(f"    !! {home} v {away}: {exc}")
                continue
            known = latest_prices(conn, match_id)
            pending: List[tuple] = []
            for bk in data.get("bookmakers", []):
                for m in bk.get("markets", []):
                    market = odds_api.canonical_market(m["key"])
                    if market is None:
                        continue
                    pending += changed_snapshots(
                        match_id, bk["key"], market, m["outcomes"], known)
                    if bk["key"] in SHARP_BOOKS:
                        fits += fit_and_store(conn, match_id, bk["key"], market, m["outcomes"])
            if pending:
                # executemany is pipelined in psycopg3, so this is a handful of
                # round trips rather than one per row.
                with conn.cursor() as cur:
                    cur.executemany(
                        """insert into odds_snapshots
                               (match_id, bookmaker, market, line, side, price)
                           values (%s,%s,%s,%s,%s,%s)""", pending)
                r.add(len(pending))
            conn.commit()
            done += 1
            print(f"    [{done}/{len(matches)}] {home[:20]:20s} v {away[:20]:20s} "
                  f"{len(pending):4d} new prices", flush=True)
        r.meta["credits_used"] = odds_api.USAGE.total_cost
        r.meta["credits_remaining"] = odds_api.USAGE.remaining
        r.meta["fits"] = fits
        print(f"  ladder fits stored: {fits}")
        print(f"  credits used {odds_api.USAGE.total_cost}, "
              f"remaining {odds_api.USAGE.remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
