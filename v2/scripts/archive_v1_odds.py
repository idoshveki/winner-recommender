"""Copy v1's odds_raw into Postgres, so the SQLite file can be pruned safely.

v1 re-inserts every bookmaker price on every run rather than writing
change-only snapshots, so odds_raw reached 412,090 rows and 71 MB (plus a
25 MB index) - 96% of a 101.41 MB database. GitHub rejects any file over
100 MB, so the Friday picks job started failing on its final commit step and
that week's picks were lost with the rejected push.

v1's picker only ever queries the next seven days of odds, so the history is
dead weight to the running system - but it is the only record of what was
actually available at the moment each live pick was made, which is what a CLV
measurement needs. Archive it, then prune.

    python -m v2.scripts.archive_v1_odds
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from v2.lib.jobs import connect, job

SQLITE = Path(__file__).resolve().parents[2] / "data" / "db" / "winner.db"
COLUMNS = ("fetched_at", "sport", "event_id", "home_team", "away_team",
           "commence_time", "bookmaker", "market", "outcome_name", "price", "point")


def main() -> int:
    with job("archive-v1-odds") as r, connect() as conn:
        conn.autocommit = False
        conn.execute("""
            create table if not exists research.v1_odds_archive (
                fetched_at    timestamptz,
                sport         text,
                event_id      text,
                home_team     text,
                away_team     text,
                commence_time timestamptz,
                bookmaker     text,
                market        text,
                outcome_name  text,
                price         double precision,
                point         double precision
            )""")
        already = conn.execute(
            "select count(*) from research.v1_odds_archive").fetchone()[0]
        if already:
            print(f"  archive already holds {already:,} rows - truncating to reload")
            conn.execute("truncate research.v1_odds_archive")

        src = sqlite3.connect(f"file:{SQLITE}?mode=ro", uri=True)
        rows = src.execute(f"select {', '.join(COLUMNS)} from odds_raw")

        # COPY, not executemany: the same 412k rows took hours row-by-row
        # against a remote pooler and seconds this way.
        copied = 0
        with conn.cursor().copy(
                f"copy research.v1_odds_archive ({', '.join(COLUMNS)}) from stdin") as cp:
            while True:
                batch = rows.fetchmany(10_000)
                if not batch:
                    break
                for row in batch:
                    cp.write_row(row)
                copied += len(batch)
                print(f"    {copied:,} rows", end="\r", flush=True)
        src.close()
        conn.commit()
        print(f"  archived {copied:,} rows to research.v1_odds_archive")
        r.add(copied)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
