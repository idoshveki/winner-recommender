"""Track v1's live picks automatically: record on issue, settle on result.

v1 emails picks every Friday and writes them to its own SQLite
(data/db/winner.db, committed to the repo). Its own settler has graded nothing
since March, so picks pile up unresolved - which is how a 3-of-3 week nearly
went unrecorded, and how two loss weeks were missed on the first hand-load.

This job removes the human from the loop:
  * every v1 leg is inserted as PENDING (hit is null) the moment it appears
  * any pending leg whose match now has a result is settled
Both halves are idempotent, so running it daily is safe.

LIVE_FROM excludes v1's backtest-seeded rows. v1 bulk-loaded 27 weeks of
backtest into its live table (weeks up to 2026-03-02) and its UI then reported
them as a live track record. Those must never enter this table.

    python -m v2.ingest.v1_picks
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from v2.lib.config import ROOT
from v2.lib.jobs import connect, job

V1_SQLITE = ROOT / "data" / "db" / "winner.db"
LIVE_FROM = "2026-03-10"          # first genuinely-live week; everything before is backtest


def resolve_team(conn, name: str) -> Optional[int]:
    row = conn.execute(
        "select team_id from team_aliases where lower(alias)=lower(%s) limit 1",
        (name.strip(),)).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "select id from teams where lower(canonical_name)=lower(%s) limit 1",
        (name.strip(),)).fetchone()
    return row[0] if row else None


def find_match(conn, text: str, week: str) -> Optional[tuple]:
    """(match_id, kickoff, hg, ag, yellows) for 'Home vs Away' near that week."""
    if not text or " vs " not in text:
        return None
    home, away = [x.strip() for x in text.split(" vs ", 1)]
    h, a = resolve_team(conn, home), resolve_team(conn, away)
    if not h or not a:
        return None
    # v1 labels a week by its Monday but generates on Friday and looks 7 days
    # ahead, so a pick can kick off up to ~12 days after the week start. A
    # narrower window silently leaves legs unresolved - which looked like a
    # pending bet when the match had actually been played and lost.
    start = week.split("/")[0]
    return conn.execute(
        """select m.id, m.kickoff_utc, s.home_goals, s.away_goals,
                  s.home_yellow + s.away_yellow
           from matches m left join match_stats s on s.match_id = m.id
           where m.home_team_id=%s and m.away_team_id=%s
             and m.kickoff_utc::date between %s::date - 2 and %s::date + 13
           order by m.kickoff_utc limit 1""",
        (h, a, start, start)).fetchone()


def grade(market: Optional[str], pick: Optional[str], hg, ag, yellows) -> Optional[bool]:
    if hg is None:
        return None
    if market and "YC" in market:
        return None if yellows is None else yellows > 3.5
    if market and "BTTS" in market:
        return hg + ag > 2.5 and hg > 0 and ag > 0
    result = "H" if hg > ag else ("A" if ag > hg else "D")
    return pick == result


def main() -> int:
    if not V1_SQLITE.exists():
        raise RuntimeError(f"v1 database not found at {V1_SQLITE}")
    v1 = sqlite3.connect(f"file:{V1_SQLITE}?mode=ro", uri=True)
    rows = v1.execute(
        """select week, generated_at, leg1_market, leg1_match, leg1_pick, leg1_odds,
                  leg2_market, leg2_match, leg2_pick, leg2_odds,
                  leg3_market, leg3_match, leg3_pick, leg3_odds,
                  draw_match, draw_odds
           from weekly_picks where week >= ? order by week""", (LIVE_FROM,)).fetchall()

    with job("track-v1-picks") as jr, connect() as conn:
        conn.autocommit = False
        inserted = settled = 0
        for r in rows:
            week, gen = r[0], r[1]
            legs = [("leg1", r[2], r[3], r[4], r[5]),
                    ("leg2", r[6], r[7], r[8], r[9]),
                    ("leg3", r[10], r[11], r[12], r[13]),
                    ("draw", "H/A", r[14], "D", r[15])]
            for leg, market, text, pick, odds in legs:
                if not text:
                    continue
                found = find_match(conn, text, week)
                mid = found[0] if found else None
                ko = found[1] if found else None
                hit = grade(market, pick, *(found[2:] if found else (None, None, None)))
                # v1 quotes an assumed 1.50 for yellow cards; 1win pays ~1.42
                odds_real = 1.42 if (market and "YC" in market) else None

                existing = conn.execute(
                    "select hit from v1_live_record where week=%s and leg=%s",
                    (week, leg)).fetchone()
                if existing is None:
                    conn.execute(
                        """insert into v1_live_record
                           (system, week, leg, market, match_text, pick, odds_quoted,
                            odds_real, hit, match_id, kickoff_utc, issued_at, settled_at, note)
                           values ('v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                   'auto-tracked from v1 sqlite')""",
                        (week, leg, market, text, pick, odds, odds_real, hit, mid, ko,
                         gen, ("now()" if hit is not None else None) and None))
                    if hit is not None:
                        conn.execute(
                            "update v1_live_record set settled_at=now() where week=%s and leg=%s",
                            (week, leg))
                    inserted += 1
                elif existing[0] is None and hit is not None:
                    conn.execute(
                        """update v1_live_record
                           set hit=%s, match_id=coalesce(match_id,%s),
                               kickoff_utc=coalesce(kickoff_utc,%s), settled_at=now()
                           where week=%s and leg=%s""",
                        (hit, mid, ko, week, leg))
                    settled += 1
        conn.commit()

        pending = conn.execute(
            "select count(*) from v1_live_record where hit is null").fetchone()[0]
        total = conn.execute("select count(*) from v1_live_record").fetchone()[0]
        print(f"  inserted {inserted} new legs, settled {settled} previously pending")
        print(f"  {total} legs tracked, {pending} still awaiting a result")
        jr.add(inserted + settled)
        jr.meta.update(inserted=inserted, settled=settled, pending=pending)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
