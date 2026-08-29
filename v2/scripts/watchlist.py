"""Which upcoming fixtures fire the card rules - i.e. which ones to screenshot.

The lead worth testing (v2/docs/FINDINGS.md, 2026-08-29): foul-heavy La Liga
matches average 5.09 yellows against 4.30, and Pinnacle under-prices them by a
little more than it under-prices other matches. Every number behind that rests
on a DERIVED 1win price, and the assumption doing the work swings the answer by
20 points. Real 1win quotes replace it.

    python -m v2.scripts.watchlist [--days 10]
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from v2.lib.jobs import connect

# thresholds from the scan, fixed before any 1win price is seen
FOULS_HOT = 28.0
CARDS_HOT = 5.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    args = ap.parse_args()

    with connect() as conn:
        # latest form snapshot per team, from its most recent completed match
        form = {}
        for tid, l5f, l5c, vc, l5p, pos in conn.execute("""
                select distinct on (f.team_id) f.team_id, f.l5_fouls, f.l5_cards,
                       f.venue_l5_cards, f.l5_points, f.position
                from team_match_form f
                join matches m on m.id = f.match_id
                order by f.team_id, m.kickoff_utc desc"""):
            form[tid] = dict(fouls=float(l5f) if l5f else None,
                             cards=float(l5c) if l5c else None,
                             venue_cards=float(vc) if vc else None,
                             l5_points=l5p, position=pos)

        rows = conn.execute("""
            select m.id, m.league, m.kickoff_utc, th.canonical_name, ta.canonical_name,
                   m.home_team_id, m.away_team_id
            from matches m
            join teams th on th.id=m.home_team_id join teams ta on ta.id=m.away_team_id
            where m.status='scheduled'
              and m.kickoff_utc between now() and now() + (%s || ' days')::interval
            order by m.kickoff_utc""", (args.days,)).fetchall()

    hot, rest = [], []
    for mid, lg, ko, home, away, hid, aid in rows:
        h, a = form.get(hid), form.get(aid)
        if not h or not a or h["fouls"] is None or a["fouls"] is None:
            continue
        fp = h["fouls"] + a["fouls"]
        cp = (h["venue_cards"] or 0) + (a["venue_cards"] or 0)
        rec = dict(league=lg, ko=ko, home=home, away=away, fouls_pred=fp, cards_pred=cp,
                   both_poor=(h["l5_points"] <= 5 and a["l5_points"] <= 5))
        (hot if fp >= FOULS_HOT else rest).append(rec)

    hot.sort(key=lambda r: -r["fouls_pred"])
    print(f"=== SCREENSHOT THESE — fouls_pred >= {FOULS_HOT} (next {args.days} days) ===\n")
    if not hot:
        print("  none qualify in this window.\n")
    print(f"  {'kickoff':16s} {'league':11s} {'match':40s} {'fouls':>6s} {'cards':>6s} {'flags'}")
    for r in hot:
        flags = "both-poor-form" if r["both_poor"] else ""
        print(f"  {str(r['ko'])[:16]:16s} {r['league']:11s} "
              f"{(r['home']+' v '+r['away'])[:40]:40s} {r['fouls_pred']:6.1f} "
              f"{r['cards_pred']:6.1f}  {flags}")

    print(f"\n=== next closest, for reference ===")
    for r in sorted(rest, key=lambda r: -r["fouls_pred"])[:6]:
        print(f"  {str(r['ko'])[:16]:16s} {r['league']:11s} "
              f"{(r['home']+' v '+r['away'])[:40]:40s} {r['fouls_pred']:6.1f} {r['cards_pred']:6.1f}")

    print(f"\n  Capture 1win's 'Yellow cards. Total' for the top block: the line,")
    print(f"  and BOTH the over and under price (the under is needed to measure margin).")
    print(f"  Target ~25 quotes. Nothing gets staked until they exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
