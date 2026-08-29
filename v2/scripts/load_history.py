"""Load teams, aliases and the football-data match history into Postgres.

Idempotent: safe to re-run. Resolution of a football-data name to a team is an
exact lookup against team_aliases; an unmapped name is recorded in
unresolved_aliases and aborts the load. v1's equivalent used NAME_MAP.get(x, x),
which passed unknown names through unchanged and silently dropped the fixture.

    python -m v2.scripts.load_history
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict

import psycopg

from v2.lib.config import DATABASE_URL, V2

CACHE = V2 / ".cache" / "football_data.json"
TEAM_MAP = V2 / "db" / "team_map.json"
DEFAULT_KICKOFF = "15:00"          # football-data omits Time for older seasons

ODDS_FIELDS = [
    "pinnacle_close_h", "pinnacle_close_d", "pinnacle_close_a",
    "pinnacle_open_h", "pinnacle_open_d", "pinnacle_open_a",
    "b365_h", "b365_d", "b365_a", "avg_h", "avg_d", "avg_a",
    "pinnacle_close_over25", "pinnacle_close_under25",
    "avg_close_over25", "avg_close_under25",
    "avg_open_over25", "avg_open_under25", "ah_line", "avg_ah_home", "avg_ah_away",
]
STAT_FIELDS = [
    "home_goals", "away_goals", "ht_home_goals", "ht_away_goals",
    "home_shots", "away_shots", "home_shots_ot", "away_shots_ot",
    "home_corners", "away_corners", "home_yellow", "away_yellow",
    "home_red", "away_red", "home_fouls", "away_fouls",
]


def load_teams(conn) -> Dict[str, int]:
    """Insert canonical teams + football-data aliases. Returns {'league|fd_name': team_id}."""
    team_map = json.loads(TEAM_MAP.read_text())
    resolved: Dict[str, int] = {}
    for key, entry in sorted(team_map.items()):
        if key.startswith("_"):
            continue
        row = conn.execute(
            """insert into teams (sofascore_id, canonical_name, league)
               values (%s, %s, %s)
               on conflict (sofascore_id) do update set canonical_name = excluded.canonical_name
               returning id""",
            (entry["sofascore_id"], entry["sofascore_name"], entry["league"]),
        ).fetchone()
        team_id = row[0]
        conn.execute(
            """insert into team_aliases (alias, source, team_id) values (%s, 'football_data', %s)
               on conflict (alias, source) do update set team_id = excluded.team_id""",
            (entry["football_data"], team_id),
        )
        conn.execute(
            """insert into team_aliases (alias, source, team_id) values (%s, 'sofascore', %s)
               on conflict (alias, source) do nothing""",
            (entry["sofascore_name"], team_id),
        )
        resolved[key] = team_id
    return resolved


def kickoff(row) -> datetime:
    t = (row.get("time") or DEFAULT_KICKOFF).strip() or DEFAULT_KICKOFF
    try:
        return datetime.strptime(f"{row['date']} {t}", "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return datetime.strptime(f"{row['date']} {DEFAULT_KICKOFF}", "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc
        )


def main() -> int:
    rows = json.loads(CACHE.read_text())
    print(f"loaded {len(rows)} matches from cache")

    with psycopg.connect(DATABASE_URL, connect_timeout=30) as conn:
        conn.autocommit = False
        teams = load_teams(conn)
        conn.commit()
        print(f"teams: {len(teams)}")

        # Resolve locally, then bulk-load. One statement per match would be
        # ~26k round trips; against a Tokyo pooler at ~550ms that is four hours.
        unresolved, match_rows, skipped = set(), [], 0
        for r in rows:
            hk = f"{r['league']}|{r['home_team']}"
            ak = f"{r['league']}|{r['away_team']}"
            if hk not in teams or ak not in teams:
                unresolved.update(k for k in (hk, ak) if k not in teams)
                skipped += 1
                continue
            match_rows.append((r, teams[hk], teams[ak]))

        if unresolved:
            for key in sorted(unresolved):
                league, alias = key.split("|", 1)
                conn.execute(
                    """insert into unresolved_aliases (alias, source, context)
                       values (%s, 'football_data', %s)
                       on conflict (alias, source) do update
                         set seen_count = unresolved_aliases.seen_count + 1,
                             last_seen = now()""",
                    (alias, json.dumps({"league": league})),
                )
            conn.commit()
            print(f"UNRESOLVED ALIASES ({len(unresolved)}): {sorted(unresolved)}")
            return 1

        print(f"staging {len(match_rows)} matches ...")
        conn.execute("""
            create temp table stage (
                league text, season int, kickoff_utc timestamptz, referee text,
                home_team_id bigint, away_team_id bigint,
                """ + ", ".join(f"{f} smallint" for f in STAT_FIELDS) + """,
                """ + ", ".join(f"{f} numeric" for f in ODDS_FIELDS) + """
            ) on commit drop""")

        cols = (["league", "season", "kickoff_utc", "referee",
                 "home_team_id", "away_team_id"] + STAT_FIELDS + ODDS_FIELDS)
        with conn.cursor().copy(
            f"copy stage ({', '.join(cols)}) from stdin"
        ) as copy:
            for r, home_id, away_id in match_rows:
                copy.write_row([
                    r["league"], r["season"], kickoff(r), r.get("referee"),
                    home_id, away_id,
                    *[r.get(f) for f in STAT_FIELDS],
                    *[r.get(f) for f in ODDS_FIELDS],
                ])

        print("inserting matches ...")
        conn.execute("""
            insert into matches (league, season, kickoff_utc, home_team_id,
                                 away_team_id, referee, status)
            select league, season, kickoff_utc, home_team_id, away_team_id,
                   referee, 'finished'
            from stage
            on conflict (league, kickoff_utc, home_team_id, away_team_id)
              do update set season = excluded.season,
                            referee = coalesce(excluded.referee, matches.referee)""")

        print("inserting stats ...")
        conn.execute(f"""
            insert into match_stats (match_id, {', '.join(STAT_FIELDS)}, source)
            select m.id, {', '.join('s.' + f for f in STAT_FIELDS)}, 'football_data'
            from stage s
            join matches m on m.league = s.league
                          and m.kickoff_utc = s.kickoff_utc
                          and m.home_team_id = s.home_team_id
                          and m.away_team_id = s.away_team_id
            on conflict (match_id) do update set
            {', '.join(f'{f} = excluded.{f}' for f in STAT_FIELDS)}""")

        print("inserting odds ...")
        conn.execute(f"""
            insert into historical_odds (match_id, {', '.join(ODDS_FIELDS)})
            select m.id, {', '.join('s.' + f for f in ODDS_FIELDS)}
            from stage s
            join matches m on m.league = s.league
                          and m.kickoff_utc = s.kickoff_utc
                          and m.home_team_id = s.home_team_id
                          and m.away_team_id = s.away_team_id
            where {' or '.join(f's.{f} is not null' for f in ODDS_FIELDS)}
            on conflict (match_id) do update set
            {', '.join(f'{f} = excluded.{f}' for f in ODDS_FIELDS)}""")
        conn.commit()

    print(f"\nloaded {len(match_rows)}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
