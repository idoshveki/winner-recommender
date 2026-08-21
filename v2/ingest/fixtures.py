"""Upcoming fixtures from SofaScore.

Note what is absent: any name matching. SofaScore's team ids are v2's canonical
ids, so a fixture resolves by integer join. v1 spent three hand-maintained
dicts and a fuzzy matcher on this problem and still dropped fixtures silently.

    python -m v2.ingest.fixtures [--days 8]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import List

from v2.ingest import sofascore
from v2.lib.config import LEAGUES, TOURNAMENT_TO_LEAGUE
from v2.lib.jobs import connect, job


def season_start_year(label: str) -> int:
    head = label.split("/")[0].strip()
    n = int(head)
    return n if len(head) == 4 else (2000 + n if n <= 30 else 1900 + n)


def collect(days: int) -> List[dict]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    out = []
    for league, (tid, _, _) in LEAGUES.items():
        seasons = sofascore.seasons(tid)
        if not seasons:
            print(f"  {league}: no seasons returned")
            continue
        season_id = seasons[0]["id"]
        season_year = season_start_year(seasons[0]["year"])
        events = sofascore.upcoming_fixtures(tid, season_id)
        n = 0
        for e in events:
            etid = (e.get("tournament", {}).get("uniqueTournament") or {}).get("id")
            if TOURNAMENT_TO_LEAGUE.get(etid) != league:
                continue
            if (e.get("status", {}).get("type") or "") in ("finished", "cancelled"):
                continue
            ts = e.get("startTimestamp")
            if ts is None:
                continue
            ko = datetime.fromtimestamp(ts, tz=timezone.utc)
            if not (now - timedelta(hours=3) <= ko <= horizon):
                continue
            out.append({
                "sofascore_event_id": e["id"],
                "league": league,
                "season": season_year,
                "kickoff_utc": ko,
                "home_sofascore_id": e["homeTeam"]["id"],
                "away_sofascore_id": e["awayTeam"]["id"],
                "home_name": e["homeTeam"]["name"],
                "away_name": e["awayTeam"]["name"],
            })
            n += 1
        print(f"  {league:11s} {n} fixtures in the next {days} days")
    return out


def store(fixtures: List[dict]) -> int:
    written = 0
    with connect() as conn:
        conn.autocommit = False
        for f in fixtures:
            # Teams must already exist. A genuinely new club (promotion) is a
            # real event that needs a canonical row, so create it rather than
            # dropping the fixture - but record the name we were given.
            for side in ("home", "away"):
                conn.execute(
                    """insert into teams (sofascore_id, canonical_name, league)
                       values (%s, %s, %s)
                       on conflict (sofascore_id) do nothing""",
                    (f[f"{side}_sofascore_id"], f[f"{side}_name"], f["league"]),
                )
                conn.execute(
                    """insert into team_aliases (alias, source, team_id)
                       select %s, 'sofascore', id from teams where sofascore_id = %s
                       on conflict (alias, source) do nothing""",
                    (f[f"{side}_name"], f[f"{side}_sofascore_id"]),
                )
            ids = conn.execute(
                """select
                     (select id from teams where sofascore_id=%s),
                     (select id from teams where sofascore_id=%s)""",
                (f["home_sofascore_id"], f["away_sofascore_id"]),
            ).fetchone()
            if not ids[0] or not ids[1]:
                raise RuntimeError(f"could not resolve teams for {f}")
            conn.execute(
                """insert into matches (sofascore_event_id, league, season,
                                        kickoff_utc, home_team_id, away_team_id, status)
                   values (%s, %s, %s, %s, %s, %s, 'scheduled')
                   on conflict (sofascore_event_id) do update
                     set kickoff_utc = excluded.kickoff_utc,
                         status = case when matches.status = 'finished'
                                       then matches.status else excluded.status end""",
                (f["sofascore_event_id"], f["league"], f["season"], f["kickoff_utc"],
                 ids[0], ids[1]),
            )
            written += 1
        conn.commit()
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    args = ap.parse_args()
    with job("ingest-fixtures") as r:
        fixtures = collect(args.days)
        r.add(store(fixtures))
        r.meta["days"] = args.days
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
