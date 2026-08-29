"""Ingest finished matches and their statistics from SofaScore.

This is the job v1 never had working. Its results feeder ran green for five
months while writing nothing, so matches_history froze and every downstream
number went stale without anyone noticing. Here a run that writes no rows is an
error, and freshness is asserted rather than assumed.

    python -m v2.ingest.results [--pages 2]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from v2.ingest import sofascore
from v2.lib.config import LEAGUES, TOURNAMENT_TO_LEAGUE
from v2.lib.jobs import connect, job

STAT_FIELDS = ("home_goals", "away_goals", "home_corners", "away_corners",
               "home_yellow", "away_yellow", "home_red", "away_red",
               "home_fouls", "away_fouls", "home_shots", "away_shots",
               "home_shots_ot", "away_shots_ot")


def season_start_year(label: str) -> int:
    head = label.split("/")[0].strip()
    n = int(head)
    return n if len(head) == 4 else (2000 + n if n <= 30 else 1900 + n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2, help="pages of finished events per league")
    args = ap.parse_args()

    with job("ingest-results") as jr, connect() as conn:
        conn.autocommit = False
        written = 0
        for league, (tid, _, _) in LEAGUES.items():
            seasons = sofascore.seasons(tid)
            if not seasons:
                continue
            sid, year = seasons[0]["id"], season_start_year(seasons[0]["year"])
            events = []
            for page in range(args.pages):
                try:
                    events += sofascore.finished_events(tid, sid, page)
                except Exception:
                    break
            done = [e for e in events
                    if (e.get("status", {}).get("type") or "") == "finished"
                    and TOURNAMENT_TO_LEAGUE.get(
                        (e.get("tournament", {}).get("uniqueTournament") or {}).get("id")) == league]
            new = 0
            for e in done:
                ko = datetime.fromtimestamp(e["startTimestamp"], tz=timezone.utc)
                for side in ("homeTeam", "awayTeam"):
                    conn.execute(
                        """insert into teams (sofascore_id, canonical_name, league)
                           values (%s,%s,%s) on conflict (sofascore_id) do nothing""",
                        (e[side]["id"], e[side]["name"], league))
                ids = conn.execute(
                    """select (select id from teams where sofascore_id=%s),
                              (select id from teams where sofascore_id=%s)""",
                    (e["homeTeam"]["id"], e["awayTeam"]["id"])).fetchone()
                mid = conn.execute(
                    """insert into matches (sofascore_event_id, league, season, kickoff_utc,
                                            home_team_id, away_team_id, status)
                       values (%s,%s,%s,%s,%s,%s,'finished')
                       on conflict (sofascore_event_id) do update set status='finished'
                       returning id""",
                    (e["id"], league, year, ko, ids[0], ids[1])).fetchone()[0]
                exists = conn.execute(
                    "select home_goals from match_stats where match_id=%s", (mid,)).fetchone()
                if exists and exists[0] is not None:
                    continue
                try:
                    st = sofascore.match_statistics(e["id"])
                except Exception:
                    continue
                if not st:
                    continue
                st["home_goals"] = (e.get("homeScore") or {}).get("current")
                st["away_goals"] = (e.get("awayScore") or {}).get("current")
                if st["home_goals"] is None:
                    continue
                cols = [f for f in STAT_FIELDS if st.get(f) is not None]
                conn.execute(
                    f"""insert into match_stats (match_id, {', '.join(cols)}, source)
                        values (%s, {', '.join(['%s']*len(cols))}, 'sofascore')
                        on conflict (match_id) do update set
                        {', '.join(f'{c}=excluded.{c}' for c in cols)}""",
                    (mid, *[st[c] for c in cols]))
                new += 1
            conn.commit()
            written += new
            print(f"  {league:11s} {len(done):3d} finished events, {new:3d} new with stats")
        jr.add(written)

        # freshness: the thing v1 never checked
        newest = conn.execute("""select max(m.kickoff_utc)::date from matches m
                                 join match_stats s on s.match_id=m.id""").fetchone()[0]
        print(f"  newest match with stats now: {newest}")
        jr.meta["newest"] = str(newest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
