"""Establish how many red cards were second yellows, per match.

SofaScore's statistics endpoint counts a second yellow twice: once in the
yellow total and again, as a dismissal, in the red total. Our cards figure was
yellows + 2*reds, so a player sent off for two bookings was counted four times
rather than the three a bookmaker settles. Every affected match reads one card
too high, and always in the same direction - toward Over.

Only the incidents endpoint distinguishes a straight red ("red") from a second
yellow ("yellowRed"), and only matches that actually contain a red card can
contain a second-yellow dismissal, so this asks about 1,383 matches rather than
all 8,912.

    python -m v2.scripts.backfill_second_yellows [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse

from v2.ingest import sofascore
from v2.lib.jobs import connect, job


def count_second_yellows(event_id: int) -> tuple[int, int]:
    """(second-yellow dismissals, straight reds) for one event."""
    incidents = sofascore._get(f"/event/{event_id}/incidents").get("incidents", [])
    second = straight = 0
    for i in incidents:
        if i.get("incidentType") != "card":
            continue
        cls = i.get("incidentClass")
        if cls == "yellowRed":
            second += 1
        elif cls == "red":
            straight += 1
    return second, straight


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with connect() as conn:
        todo = conn.execute(
            """select m.id, m.sofascore_event_id, m.kickoff_utc::date,
                      coalesce(s.home_red,0)+coalesce(s.away_red,0)
                 from matches m join match_stats s on s.match_id = m.id
                where s.second_yellow_reds is null
                  and m.sofascore_event_id is not null
                order by m.kickoff_utc desc""").fetchall()
    if args.limit:
        todo = todo[:args.limit]

    remaining = sofascore.credits_remaining()
    print(f"  {len(todo):,} matches to establish; {remaining:,} credits remaining")
    if remaining is not None and len(todo) > remaining:
        raise SystemExit(
            f"refusing to start: {len(todo)} calls needed, {remaining} credits left")
    if args.dry_run:
        print("  dry run, nothing fetched")
        return 0

    with job("backfill-second-yellows", expect_rows=False) as r, connect() as conn:
        conn.autocommit = False
        done = failed = with_second = 0
        for match_id, event_id, day, n_red in todo:
            try:
                second, straight = count_second_yellows(event_id)
            except Exception as exc:
                # Leave it null rather than guessing zero: null means "not
                # established", and a wrong zero would silently keep the old
                # inflated total while looking resolved.
                failed += 1
                if failed <= 5:
                    print(f"    !! {day} event {event_id}: {str(exc)[:80]}")
                continue
            # Trust the statistics endpoint for the red count and use incidents
            # only to split it; if they disagree, cap so the subtraction can
            # never exceed the reds actually recorded.
            second = min(second, n_red)
            conn.execute(
                "update match_stats set second_yellow_reds=%s where match_id=%s",
                (second, match_id))
            done += 1
            with_second += 1 if second else 0
            if done % 100 == 0:
                conn.commit()
                print(f"    {done:,}/{len(todo):,} done, {with_second:,} had a "
                      f"second yellow", flush=True)
        conn.commit()
        r.add(done)
        r.meta.update(done=done, failed=failed, with_second=with_second)
        print(f"\n  established {done:,} matches; {with_second:,} contained a "
              f"second-yellow dismissal; {failed} could not be read")
        print(f"  credits remaining: {sofascore.credits_remaining()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
