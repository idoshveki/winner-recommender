"""Pull historical Pinnacle cards/corners ladders for matches we already have
results for, so the model can be backtested against real prices.

Snapshots are taken at kickoff minus SNAPSHOT_HOURS, so nothing the model sees
was published after the bet would have been placed.

Costs 10 credits per market per region per event (historical is 10x live), so
runs are budgeted and event lists are cached per (sport, hour).

    python -m v2.ingest.historical_prices --from 2026-02-01 --to 2026-05-24 --limit 120
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

from v2.ingest import odds_api
from v2.lib.config import V2
from v2.lib.http import get
from v2.lib.jobs import connect, job
from v2.lib.names import best_match

BASE = "https://api.the-odds-api.com/v4"
SNAPSHOT_HOURS = 3
DEFAULT_MARKETS = ["alternate_spreads_cards", "alternate_totals_corners"]
REGIONS = "eu"                      # pinnacle lives here; 1 region keeps cost down
def credits_per_event(markets):
    return 10 * len(markets) * len(REGIONS.split(","))
CACHE = V2 / ".cache" / "historical_events"


def historical_events(sport: str, iso_time: str) -> List[dict]:
    """Event list as it stood at `iso_time`. 1 credit, cached on disk."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{sport}_{iso_time.replace(':', '').replace('-', '')}.json"
    if path.exists():
        return json.loads(path.read_text())
    resp = get(f"{BASE}/historical/sports/{sport}/events",
               params={"apiKey": odds_api._key(), "date": iso_time})
    odds_api.USAGE.record(resp.headers)
    data = resp.json().get("data", [])
    path.write_text(json.dumps(data))
    return data


def historical_event_odds(sport: str, event_id: str, iso_time: str, markets) -> dict:
    resp = get(f"{BASE}/historical/sports/{sport}/events/{event_id}/odds",
               params={"apiKey": odds_api._key(), "date": iso_time,
                       "regions": REGIONS, "markets": ",".join(markets),
                       "oddsFormat": "decimal"})
    odds_api.USAGE.record(resp.headers)
    return resp.json().get("data", {})


def target_matches(conn, date_from: str, date_to: str, limit: int,
                   league: str = None, skip_existing_market: str = None) -> List[tuple]:
    """Finished matches with stats, evenly spread over the window.

    Ordered by kickoff and then sampled with a fixed stride rather than
    ORDER BY random(), so the sample is reproducible and covers the whole
    period instead of clustering.
    """
    rows = conn.execute(
        """select m.id, m.league, m.kickoff_utc, th.canonical_name, ta.canonical_name,
                  s.home_yellow + s.away_yellow, s.home_corners + s.away_corners
           from matches m
           join match_stats s on s.match_id = m.id
           join teams th on th.id = m.home_team_id
           join teams ta on ta.id = m.away_team_id
           where m.kickoff_utc::date between %s and %s
             and s.home_yellow is not null
             and (%s::text is null or m.league = %s)
             and (%s::text is null or m.id not in (
                   select match_id from odds_snapshots where market = %s::text))
           order by m.kickoff_utc, m.id""",
        (date_from, date_to, league, league, skip_existing_market, skip_existing_market),
    ).fetchall()
    if limit and len(rows) > limit:
        stride = len(rows) / limit
        rows = [rows[int(i * stride)] for i in range(limit)]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to", required=True)
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--budget", type=int, default=13000)
    ap.add_argument("--markets", default=",".join(DEFAULT_MARKETS))
    ap.add_argument("--league", default=None)
    ap.add_argument("--skip-market", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with connect() as conn:
        targets = target_matches(conn, args.date_from, args.date_to, args.limit,
                                 args.league, args.skip_market)
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    per = credits_per_event(markets)
    cost = len(targets) * per
    print(f"  {len(targets)} matches x {len(markets)} markets, up to {cost} credits "
          f"({per}/event), budget {args.budget}")
    print(f"  markets: {', '.join(markets)}")
    if cost > args.budget:
        raise SystemExit("refusing to run: over budget")
    if args.dry_run:
        return 0

    with job("fetch-historical-prices") as r, connect() as conn:
        conn.autocommit = False
        matched = missed = 0
        for match_id, league, ko, home, away, _, _ in targets:
            sport = odds_api.sport_key(league)
            snap = (ko - timedelta(hours=SNAPSHOT_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                events = historical_events(sport, snap)
            except Exception as exc:
                print(f"    !! events {sport} {snap}: {exc}")
                missed += 1
                continue
            cands = [(f"{e['home_team']} v {e['away_team']}", e) for e in events]
            event, score, _ = best_match(f"{home} v {away}", cands, threshold=0.60)
            if event is None:
                missed += 1
                continue
            try:
                data = historical_event_odds(sport, event["id"], snap, markets)
            except Exception as exc:
                print(f"    !! odds {home} v {away}: {exc}")
                missed += 1
                continue
            wrote = 0
            for bk in data.get("bookmakers", []):
                for m in bk.get("markets", []):
                    market = odds_api.canonical_market(m["key"])
                    if market is None:
                        continue
                    for o in m["outcomes"]:
                        if o.get("point") is None:
                            continue
                        conn.execute(
                            """insert into odds_snapshots
                                 (match_id, bookmaker, market, line, side, price, fetched_at)
                               values (%s,%s,%s,%s,%s,%s,%s)""",
                            (match_id, bk["key"], market, float(o["point"]),
                             o["name"], float(o["price"]),
                             ko - timedelta(hours=SNAPSHOT_HOURS)),
                        )
                        wrote += 1
            if wrote:
                matched += 1
                r.add(wrote)
            conn.commit()
            if matched and matched % 20 == 0:
                print(f"    {matched} matched, {odds_api.USAGE.total_cost} credits used")
        conn.commit()
        r.meta["credits_used"] = odds_api.USAGE.total_cost
        r.meta["credits_remaining"] = odds_api.USAGE.remaining
        r.meta["matched"] = matched
        r.meta["missed"] = missed
        print(f"  matched {matched}, missed {missed}, "
              f"credits {odds_api.USAGE.total_cost} (remaining {odds_api.USAGE.remaining})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
