"""Feed v2's fresh results back into v1's matches_history.

v1's results feeder has written nothing since 2026-03-09 - it reads gitignored
CSVs that are not there, and ran green for five months while doing nothing.
Everything v1 derives from history has been seven months stale ever since, and
the yellow-card averages are the part that matters: yc_pred is
home_yc_avg + away_yc_avg over each team's last five venue matches, so with no
current-season rows the `yc_pred >= 6.0` gate can never fire. v1 has not
emailed a single card pick since March, which is the market it was kept for.

v2 ingests the same matches into Postgres every morning and is current. This
copies them across so v1 works off live data without changing v1's logic.

Names are the whole difficulty. v1's history uses football-data's spellings
("Wolves", "Schalke 04"); v2 uses SofaScore's ("Wolverhampton",
"FC Schalke 04"). v1's own NAME_MAP already covers 77 of the 90 current teams.
Of the remaining 13, six are unambiguous and are mapped explicitly below; the
other seven are clubs with no top-flight history in our window (promoted sides
like Elversberg and Paderborn), and they correctly accumulate history under
their own name - the fixture lookup passes unmapped names through unchanged,
so both sides agree.

Nothing is guessed by string similarity. v2's own matcher scored
"Malaga CF" against "Mallorca" at 0.62 and "Hull City"/"Coventry City" against
"Man City" at 0.40 - different clubs, and accepting any of them would blend one
team's cards into another's average. Same failure as "Man United" matching
"Manchester City" earlier in this project. A missing team is visible; a wrong
team is not.

    python -m v2.scripts.sync_v1_history [--since 2026-03-09]
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from v2.lib.jobs import connect, job

SQLITE = Path(__file__).resolve().parents[2] / "data" / "db" / "winner.db"

# SofaScore spelling -> v1's football-data spelling. Only exact-confidence
# cases: each of these scored 0.74-1.00 against exactly one history name.
EXTRA_NAMES = {
    "FC Barcelona":  "Barcelona",
    "FC Schalke 04": "Schalke 04",
    "Liverpool FC":  "Liverpool",
    "SSC Napoli":    "Napoli",
    "Ipswich Town":  "Ipswich",
    "Wolverhampton": "Wolves",
}

STATS = ("home_corners", "away_corners", "home_yellow", "away_yellow",
         "home_red", "away_red", "home_shots", "away_shots",
         "home_shots_ot", "away_shots_ot")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-03-09",
                    help="only copy matches kicking off after this date")
    args = ap.parse_args()

    from src.recommend.send_weekly import NAME_MAP
    mapping = {**NAME_MAP, **EXTRA_NAMES}

    with job("sync-v1-history") as r, connect() as pg:
        rows = pg.execute(f"""
            select m.league, m.season, m.kickoff_utc::date,
                   th.canonical_name, ta.canonical_name,
                   s.home_goals, s.away_goals,
                   {', '.join('s.' + c for c in STATS)}
              from matches m
              join match_stats s on s.match_id = m.id
              join teams th on th.id = m.home_team_id
              join teams ta on ta.id = m.away_team_id
             where m.kickoff_utc > %s
               and s.home_goals is not null
             order by m.kickoff_utc""", (args.since,)).fetchall()
        print(f"  {len(rows)} finished matches in Postgres after {args.since}")

        con = sqlite3.connect(SQLITE)
        known = {t for (t,) in con.execute(
            "select distinct home_team from matches_history")} | {
            t for (t,) in con.execute(
            "select distinct away_team from matches_history")}

        before = con.total_changes
        unresolved: dict[str, int] = {}
        for (league, season, date, home, away, hg, ag, *stats) in rows:
            h = mapping.get(home, home)
            a = mapping.get(away, away)
            for original, resolved in ((home, h), (away, a)):
                if resolved not in known:
                    unresolved[original] = unresolved.get(original, 0) + 1
            result = "H" if hg > ag else ("A" if ag > hg else "D")
            con.execute(
                f"""insert or ignore into matches_history
                    (league, season, date, home_team, away_team,
                     home_goals, away_goals, result, {', '.join(STATS)})
                    values ({', '.join(['?'] * (8 + len(STATS)))})""",
                (league, season, str(date), h, a, hg, ag, result, *stats))
        con.commit()

        # total_changes, not the number of statements run: insert-or-ignore
        # reports success for every row it silently drops, and counting
        # attempts instead of writes is how a whole week of v2 picks went
        # missing earlier while the job printed "recorded 1 picks".
        written = con.total_changes - before
        newest = con.execute(
            "select max(date) from matches_history").fetchone()[0]
        con.close()

        print(f"  inserted {written} new rows; matches_history now ends {newest}")
        if unresolved:
            print(f"  {len(unresolved)} team name(s) with no history to join to "
                  f"(they will build their own from here):")
            for name, n in sorted(unresolved.items(), key=lambda kv: -kv[1]):
                print(f"    {name!r} in {n} match(es)")
        r.add(written)
        r.meta["newest"] = str(newest)
        r.meta["unresolved"] = sorted(unresolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
