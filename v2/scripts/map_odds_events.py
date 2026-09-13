"""Bootstrap odds_api team aliases and link Odds API events to our matches.

Costs zero credits: only the free /events endpoint is used.

    python -m v2.scripts.map_odds_events
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from v2.ingest import odds_api
from v2.lib.config import LEAGUES
from v2.lib.jobs import connect, job
from v2.lib.names import best_match

AUTO_THRESHOLD = 0.70
MANUAL = Path(__file__).resolve().parents[1] / "db" / "odds_api_aliases_manual.json"


def manual_aliases(league: str) -> dict:
    if not MANUAL.exists():
        return {}
    return {k: v for k, v in json.loads(MANUAL.read_text()).get(league, {}).items()}


def main() -> int:
    with job("map-odds-events") as r, connect() as conn:
        conn.autocommit = False
        unresolved = []
        linked = 0
        for league, (_, sport, _) in LEAGUES.items():
            teams = conn.execute(
                "select id, canonical_name from teams where league = %s", (league,)
            ).fetchall()
            candidates = [(name, tid) for tid, name in teams]
            existing = dict(conn.execute(
                """select a.alias, a.team_id from team_aliases a
                   join teams t on t.id = a.team_id
                   where a.source = 'odds_api' and t.league = %s""", (league,)
            ).fetchall())

            manual = manual_aliases(league)
            by_name = {name: tid for name, tid in candidates}

            events = odds_api.list_events(sport)
            resolved_here = 0
            for e in events:
                ids = {}
                for side in ("home_team", "away_team"):
                    name = e[side]
                    if name in existing:
                        ids[side] = existing[name]
                        continue
                    # reviewed decisions win over anything automatic
                    if name in manual and manual[name] in by_name:
                        team_id, score = by_name[manual[name]], 1.0
                    else:
                        team_id, score, _ = best_match(
                            name, candidates, threshold=AUTO_THRESHOLD)
                    if team_id is None:
                        unresolved.append((league, name, round(score, 3)))
                        continue
                    conn.execute(
                        """insert into team_aliases (alias, source, team_id)
                           values (%s, 'odds_api', %s)
                           on conflict (alias, source) do nothing""",
                        (name, team_id),
                    )
                    existing[name] = team_id
                    ids[side] = team_id
                if len(ids) != 2:
                    continue
                # link by team pair within a day of the stored kickoff
                updated = conn.execute(
                    """update matches set odds_api_event_id = %s
                       where home_team_id = %s and away_team_id = %s
                         and kickoff_utc between %s::timestamptz - interval '1 day'
                                             and %s::timestamptz + interval '1 day'
                         and (odds_api_event_id is null or odds_api_event_id = %s)
                       returning id""",
                    (e["id"], ids["home_team"], ids["away_team"],
                     e["commence_time"], e["commence_time"], e["id"]),
                ).fetchall()
                linked += len(updated)
                resolved_here += 1
            print(f"  {league:11s} {len(events):3d} events, {resolved_here} resolved")

        for league, name, score in unresolved:
            conn.execute(
                """insert into unresolved_aliases (alias, source, context)
                   values (%s, 'odds_api', %s)
                   on conflict (alias, source) do update
                     set seen_count = unresolved_aliases.seen_count + 1, last_seen = now()""",
                (name, f'{{"league": "{league}", "best_score": {score}}}'),
            )
        conn.commit()
        r.add(linked)
        r.meta["credits_used"] = odds_api.USAGE.total_cost
        r.meta["credits_remaining"] = odds_api.USAGE.remaining
        print(f"  linked {linked} matches to Odds API events")
        print(f"  credits used: {odds_api.USAGE.total_cost} "
              f"(remaining {odds_api.USAGE.remaining})")
        if unresolved:
            print(f"  UNRESOLVED ({len(unresolved)}): {unresolved[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
