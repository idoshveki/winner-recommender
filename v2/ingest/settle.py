"""Settle any pending pick, from either system, against ingested results.

v1's picks were settled by v1_picks.py as a side effect of re-reading v1's
SQLite. v2's picks had no settler at all, so two of them sat pending with
results already in the database: Inter v Udinese (2 yellows, Under 3.5 won) and
Leeds v Newcastle (3 yellows, Over 3.5 lost).

This settles by match_id, so it does not care which system produced the pick or
where it came from. Runs daily; idempotent.

    python -m v2.ingest.settle
"""
from __future__ import annotations

from typing import Optional

from v2.lib.jobs import connect, job
from v2.model.markets import settle_total


def grade(market: str, pick: str, hg: int, ag: int,
          yellows: Optional[int], cards_book: Optional[int]) -> Optional[bool]:
    """True/False, or None when the outcome cannot be determined yet."""
    m = (market or "").upper()
    pk = (pick or "").strip()

    # Two different quantities wear the word "cards" here, and settling one
    # with the other is a silent, expensive error.
    #
    #   "YC ..."    - v1's market. 1win prices YELLOW cards and carries reds
    #                 separately, so this settles on yellows alone.
    #   "Cards ..." - v2's market. It prices Pinnacle's alternate_totals_cards
    #                 and models target_cards_book, where a red counts as TWO.
    #
    # Grading v2's Over 4.5 on Freiburg v Schalke by yellows (4) would have
    # recorded a loss on a bet its own market won (6 cards).
    if "YC" in m or "CARD" in m:
        total = yellows if "YC" in m else cards_book
        if total is None:
            return None
        parts = pk.split()
        if len(parts) < 2:
            return None
        side, line = parts[0].lower(), float(parts[1])
        res = settle_total(total, line, side)
        return None if res is None else res == 1.0

    if "BTTS" in m:
        return hg + ag > 2.5 and hg > 0 and ag > 0

    result = "H" if hg > ag else ("A" if ag > hg else "D")
    return pk == result


def main() -> int:
    with job("settle", expect_rows=False) as jr, connect() as conn:
        conn.autocommit = False
        rows = conn.execute("""
            select lr.id, lr.system, lr.week, lr.leg, lr.market, lr.pick,
                   lr.match_text, s.home_goals, s.away_goals,
                   s.home_yellow + s.away_yellow,
                   -- coalesce: reds come back NULL when the feed omits them,
                   -- and 2*NULL made the entire cards total NULL, which reads
                   -- as "not gradeable yet" and would park the pick forever.
                   --
                   -- second_yellow_reds is subtracted because SofaScore counts
                   -- a second yellow twice - once as a booking and again as the
                   -- dismissal - while a book settles it as one yellow plus one
                   -- red. See v2.model.features.cards_book.
                   s.home_yellow + s.away_yellow
                     - coalesce(s.second_yellow_reds, 0)
                     + 2*(coalesce(s.home_red,0) + coalesce(s.away_red,0))
            from v1_live_record lr
            join matches m on m.id = lr.match_id
            join match_stats s on s.match_id = m.id
            where lr.hit is null and s.home_goals is not null
            order by lr.week, lr.system, lr.leg""").fetchall()

        settled = 0
        for (rid, system, week, leg, market, pick, text,
             hg, ag, yellows, cards_book) in rows:
            hit = grade(market, pick, hg, ag, yellows, cards_book)
            if hit is None:
                print(f"  {system} {week} {leg}: {text} — result present but "
                      f"not gradeable ({market} / {pick})")
                continue
            conn.execute(
                "update v1_live_record set hit=%s, settled_at=now() where id=%s",
                (hit, rid))
            settled += 1
            basis = (f"{yellows} yellows" if "YC" in (market or "").upper()
                     else f"{cards_book} cards" if "CARD" in (market or "").upper()
                     else f"{hg}-{ag}")
            print(f"  {system} {week} {leg}: {text[:34]:34s} {pick:10s} "
                  f"{basis} -> {'HIT' if hit else 'miss'}")
        conn.commit()

        still = conn.execute(
            "select count(*) from v1_live_record where hit is null").fetchone()[0]
        print(f"\n  settled {settled}; {still} still awaiting a result")
        jr.add(settled)
        jr.meta.update(settled=settled, pending=still)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
