"""Settle v1's outstanding weekly picks using v2's (current) results.

v1 records picks but has not settled one since March: its results feeder reads
gitignored CSVs that do not exist in CI, so matches_history froze and
update_pick_results.py has nothing to match against. Picks accumulate with
hit=NULL forever.

v2's data is current, and team_aliases resolves v1's football-data-style names
onto canonical ids, so the outstanding weeks can be graded properly.

    python -m v2.scripts.settle_v1_picks
"""
from __future__ import annotations

import sqlite3
import subprocess
import tempfile
from pathlib import Path

from v2.lib.jobs import connect

V1_DB = "/tmp/v1_latest.db"


def resolve(conn, name: str):
    """v1 name -> v2 team id, via the alias table."""
    row = conn.execute(
        """select team_id from team_aliases where lower(alias)=lower(%s) limit 1""",
        (name.strip(),)).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        """select id from teams where lower(canonical_name)=lower(%s) limit 1""",
        (name.strip(),)).fetchone()
    return row[0] if row else None


def find_match(conn, home: str, away: str, week: str):
    start, end = week.split("/")
    h, a = resolve(conn, home), resolve(conn, away)
    if not h or not a:
        return None, f"unresolved name ({home} / {away})"
    row = conn.execute(
        """select m.id, s.home_goals, s.away_goals, s.home_yellow, s.away_yellow,
                  s.home_red, s.away_red
           from matches m join match_stats s on s.match_id=m.id
           where m.home_team_id=%s and m.away_team_id=%s
             and m.kickoff_utc::date between %s::date - 3 and %s::date + 3""",
        (h, a, start, end)).fetchone()
    return (row, None) if row else (None, "no result yet")


def grade(market: str, pick: str, r) -> bool:
    _, hg, ag, hy, ay, hr, ar = r
    if market and "YC" in market:
        return (hy + ay) > 3.5
    if market and "BTTS" in market:
        return hg + ag > 2.5 and hg > 0 and ag > 0
    res = "H" if hg > ag else ("A" if ag > hg else "D")
    return pick == res


def main() -> int:
    con = sqlite3.connect(f"file:{V1_DB}?mode=ro", uri=True)
    rows = con.execute("""
        select week, generated_at, leg1_market, leg1_match, leg1_pick, leg1_odds,
               leg2_market, leg2_match, leg2_pick, leg2_odds,
               draw_match, draw_odds, slip_won
        from weekly_picks where slip_won is null order by week""").fetchall()
    print(f"{len(rows)} unsettled weeks in v1\n")

    with connect() as conn:
        settled = []
        for (week, gen, m1, mt1, p1, o1, m2, mt2, p2, o2, dmt, dodds, _) in rows:
            print(f"week {week}")
            legs, ok = [], True
            for label, market, match, pick, odds in (
                    ("leg1", m1, mt1, p1, o1), ("leg2", m2, mt2, p2, o2),
                    ("draw", "H/A", dmt, "D", dodds)):
                if not match:
                    continue
                try:
                    home, away = [x.strip() for x in match.split(" vs ")]
                except ValueError:
                    print(f"  {label}: cannot parse {match!r}"); ok = False; continue
                r, err = find_match(conn, home, away, week)
                if r is None:
                    print(f"  {label}: {match:38s} {err}"); ok = False; continue
                hit = grade(market, pick, r)
                legs.append((label, match, market, pick, odds, hit, r))
                print(f"  {label}: {match:38s} {(market or '')[:12]:12s} {pick or '':4s} "
                      f"@{odds}  {r[1]}-{r[2]}  {'HIT ' if hit else 'MISS'}")
            if legs:
                acc = [l for l in legs if l[0] != "draw"]
                if acc and all(l[5] for l in acc):
                    combined = 1.0
                    for l in acc: combined *= float(l[4])
                    print(f"  -> SLIP WON, combined {combined:.2f}x")
                elif acc:
                    print(f"  -> slip lost")
                settled.append((week, legs, ok))
            print()

        print("=" * 62)
        print("LIVE RECORD (v1 recommendations, graded on v2 data)")
        print("=" * 62)
        won = lost = 0
        for week, legs, ok in settled:
            acc = [l for l in legs if l[0] != "draw"]
            if not acc or not ok:
                continue
            if all(l[5] for l in acc): won += 1
            else: lost += 1
        print(f"  slips: {won} won, {lost} lost")
        allegs = [l for _, legs, _ in settled for l in legs]
        h = sum(1 for l in allegs if l[5])
        print(f"  individual legs: {h}/{len(allegs)} = {h/len(allegs):.0%}" if allegs else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
