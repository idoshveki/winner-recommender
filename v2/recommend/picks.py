"""v2's own weekly picks — recorded, not emailed, and honest about qualifying.

v1 picks every Friday whatever the numbers say. v2 uses the models that
actually survived testing, prices them against real Pinnacle odds, and records
BOTH the pick it ranks highest and whether that pick clears its edge gate.

Most weeks nothing will qualify. That is the finding, not a failure: across
8,676 matches these markets priced everything we could measure. Recording the
top-ranked pick anyway - flagged `qualifies = false` - means we can compare, a
season from now, what v2 would have bet against what v1 actually bet.

Two markets, both chosen because they are the only things that measured up:

  CARDS   the pooled gradient-boosted model. Brier 0.2105 against a 0.2190
          league base rate (+3.84%), the best predictor built here. Settled by
          the BOOK rule (yellows + 2x reds), which v1 gets wrong.

  DRAWS   v1's own draw gate, which is the one v1 component with demonstrated
          selection skill: it lifts the draw rate from 25.3% to 31.6% and holds
          out of sample. It is also priced to the decimal - break-even at its
          average 3.16 is 31.6% - so it will rarely qualify.

    python -m v2.recommend.picks [--days 8] [--min-edge 0.05] [--dry-run]
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from v2.ingest import odds_api
from v2.lib.config import LEAGUES
from v2.lib.jobs import connect, job
from v2.model import cards_models, features
from v2.model.devig import devig_shin, p_over_from_dist
from v2.model.lineshop import devig_three_way

MIN_EDGE = 0.05
CARD_LINES = (3.5, 4.5)
MIN_HISTORY = 8      # venue matches per side; promoted clubs start near zero
# Sanity ceiling from the plan, which I failed to implement first time round and
# immediately paid for: a first run produced "+60% edge" on a model probability
# 34 points away from Pinnacle's. A disagreement that size with the sharpest
# book in the world is a bug, not an edge. Anything beyond this is discarded.
MAX_DIVERGENCE = 0.10


def week_label(when: datetime) -> str:
    monday = when.date() - timedelta(days=when.weekday())
    return f"{monday}/{monday + timedelta(days=6)}"


def load_history(conn):
    return [
        dict(match_id=r[0], league=r[1], kickoff_utc=r[2], home_team_id=r[3],
             away_team_id=r[4], home_yellow=r[5], away_yellow=r[6],
             home_corners=r[7], away_corners=r[8], home_shots=r[9], away_shots=r[10],
             home_fouls=r[11], away_fouls=r[12], home_goals=r[13], away_goals=r[14],
             home_red=r[15], away_red=r[16],
             odds_h=r[17], odds_d=r[18], odds_a=r[19])
        for r in conn.execute("""
            select m.id, m.league, m.kickoff_utc, m.home_team_id, m.away_team_id,
                   s.home_yellow, s.away_yellow, s.home_corners, s.away_corners,
                   s.home_shots, s.away_shots, s.home_fouls, s.away_fouls,
                   s.home_goals, s.away_goals, s.home_red, s.away_red,
                   coalesce(o.pinnacle_close_h, o.avg_h),
                   coalesce(o.pinnacle_close_d, o.avg_d),
                   coalesce(o.pinnacle_close_a, o.avg_a)
            from matches m
            join match_stats s on s.match_id = m.id
            left join historical_odds o on o.match_id = m.id
            where m.status='finished' and s.home_yellow is not null
            order by m.kickoff_utc, m.id""")
    ]


def upcoming(conn, days: int):
    return conn.execute("""
        select m.id, m.league, m.kickoff_utc, m.odds_api_event_id,
               th.canonical_name, ta.canonical_name, m.home_team_id, m.away_team_id
        from matches m
        join teams th on th.id=m.home_team_id join teams ta on ta.id=m.away_team_id
        where m.status='scheduled' and m.odds_api_event_id is not null
          and m.kickoff_utc between now() and now() + (%s || ' days')::interval
        order by m.kickoff_utc""", (days,)).fetchall()


def draw_gate(conn, match_id, home_id, away_id, league, kickoff):
    """v1's draw gate: pd>=.29, |pts5 diff|<=1, both dr10>.20 — recomputed here."""
    def venue_hist(team_id, venue):
        col = "home_team_id" if venue == "home" else "away_team_id"
        rows = conn.execute(f"""
            select s.home_goals, s.away_goals from matches m
            join match_stats s on s.match_id=m.id
            where m.{col}=%s and m.league=%s and m.kickoff_utc < %s
            order by m.kickoff_utc desc limit 10""",
            (team_id, league, kickoff)).fetchall()
        out = []
        for hg, ag in rows:
            r = "D" if hg == ag else ("W" if (hg > ag) == (venue == "home") else "L")
            out.append(r)
        return out
    h, a = venue_hist(home_id, "home"), venue_hist(away_id, "away")
    if len(h) < 10 or len(a) < 10:
        return None
    pts = lambda xs: sum(3 if r == "W" else (1 if r == "D" else 0) for r in xs[:5])
    return dict(hdr=h.count("D") / len(h), adr=a.count("D") / len(a),
                gap=abs(pts(h) - pts(a)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--min-edge", type=float, default=MIN_EDGE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with connect() as conn:
        hist = load_history(conn)
        fixtures = upcoming(conn, args.days)
    print(f"  {len(hist)} historical matches, {len(fixtures)} upcoming fixtures")
    if not fixtures:
        print("  nothing scheduled in the window")
        return 0

    # Upcoming fixtures need feature rows too, and those rows must be built
    # from the same chronological pass as the history - otherwise a fixture has
    # no rolling form and the model silently produces nothing.
    pending_rows = [dict(match_id=mid, league=lg, kickoff_utc=ko,
                         home_team_id=hid, away_team_id=aid)
                    for mid, lg, ko, _ev, _h, _a, hid, aid in fixtures]
    feats = features.build(sorted(hist + pending_rows,
                                  key=lambda r: (str(r["kickoff_utc"]), r["match_id"])))
    train = [f for f in feats if f.usable() and f.target_cards_book is not None]
    gbm = cards_models.train_line_gbm(train, CARD_LINES)
    print(f"  cards model: {len(gbm.by_key)} league-line fits")

    candidates, skipped, diverged = [], [], []
    with connect() as conn:
        for mid, league, ko, event_id, home, away, hid, aid in fixtures:
            try:
                data = odds_api.event_odds(
                    LEAGUES[league][1], event_id,
                    ["alternate_totals_cards", "h2h"], regions="eu")
            except Exception as exc:
                print(f"    !! {home} v {away}: {exc}")
                continue
            lad, h2h = defaultdict(dict), {}
            for b in data.get("bookmakers", []):
                if b["key"] != "pinnacle":
                    continue
                for m in b.get("markets", []):
                    for o in m["outcomes"]:
                        if o.get("point") is not None:
                            lad[float(o["point"])][o["name"].lower()] = float(o["price"])
                        else:
                            h2h[o["name"]] = float(o["price"])

            # cards: needs a feature row built from this fixture's history
            row = next((f for f in feats if f.match_id == mid), None)
            # The 1X2 odds are three of the model's features. Without them the
            # design matrix imputes training means and every fixture collapses
            # to the same prediction - which is exactly what happened first run.
            if row is not None and len(h2h) == 3 and "Draw" in h2h:
                oth = [v for k, v in h2h.items() if k != "Draw"]
                _ph, _pd, _pa = devig_three_way(oth[0], h2h["Draw"], oth[1])
                row.features["mkt_p_home"] = _ph
                row.features["mkt_p_draw"] = _pd
                row.features["mkt_closeness"] = 1.0 - abs(_ph - _pa)
            if row is not None and (not row.usable() or row.n_obs_min < MIN_HISTORY
                                    or row.features.get("mkt_closeness") is None):
                skipped.append((f"{home} v {away}", row.n_obs_min))
                row = None
            two = {l: v for l, v in lad.items() if "over" in v and "under" in v}
            for line, v in sorted(two.items()):
                if line not in CARD_LINES or row is None:
                    continue
                p = gbm.p_over(row, line)
                if p is None:
                    continue
                q = devig_shin(v["over"], v["under"])
                for side, price, pp in (("Over", v["over"], p), ("Under", v["under"], 1 - p)):
                    qq = q if side == "Over" else 1 - q
                    if abs(pp - qq) > MAX_DIVERGENCE:
                        diverged.append((f"{home} v {away}", side, line, pp, qq))
                        continue
                    candidates.append(dict(
                        market="cards", pick=f"{side} {line}", match=f"{home} vs {away}",
                        match_id=mid, kickoff=ko, price=price,
                        model_prob=pp, book_prob=(q if side == "Over" else 1 - q),
                        edge=pp * price - 1))

            # draws — identify Draw by name, the other two by elimination, so a
            # mismatch between our canonical names and the book's cannot break it
            if len(h2h) == 3 and "Draw" in h2h:
                others = [v for k, v in h2h.items() if k != "Draw"]
                ph, pd_, pa = devig_three_way(others[0], h2h["Draw"], others[1])
                g = draw_gate(conn, mid, hid, aid, league, ko)
                if g and pd_ >= 0.29 and g["gap"] <= 1 and g["hdr"] > 0.20 and g["adr"] > 0.20:
                    price = h2h.get("Draw", 0)
                    if price:
                        candidates.append(dict(
                            market="draw", pick="D", match=f"{home} vs {away}",
                            match_id=mid, kickoff=ko, price=price,
                            model_prob=pd_, book_prob=pd_, edge=pd_ * price - 1))

    print(f"  credits used {odds_api.USAGE.total_cost}, remaining {odds_api.USAGE.remaining}")
    if skipped:
        print(f"  skipped {len(skipped)} fixtures with too little history "
              f"(need {MIN_HISTORY} venue matches): "
              + ", ".join(f"{m} (n={n})" for m, n in skipped[:4])
              + ("..." if len(skipped) > 4 else ""))
    if diverged:
        print(f"  DISCARDED {len(diverged)} picks diverging >{MAX_DIVERGENCE:.0%} from Pinnacle:")
        for m, sd, ln, pm, pb in diverged[:5]:
            print(f"      {m[:34]:34s} {sd} {ln}  model {pm:.1%} vs book {pb:.1%}")
    if not candidates:
        print("  no candidates could be priced")
        return 0

    candidates.sort(key=lambda c: -c["edge"])
    qualifying = [c for c in candidates if c["edge"] >= args.min_edge]
    print(f"\n  {len(candidates)} priced candidates, "
          f"{len(qualifying)} clear the {args.min_edge:.0%} edge gate\n")
    print(f"  {'market':6s} {'match':38s} {'pick':10s} {'@':>5s} {'model':>6s} {'book':>6s} {'edge':>7s}")
    for c in candidates[:10]:
        mark = " *" if c["edge"] >= args.min_edge else ""
        print(f"  {c['market']:6s} {c['match'][:38]:38s} {c['pick']:10s} {c['price']:5.2f} "
              f"{c['model_prob']:6.1%} {c['book_prob']:6.1%} {c['edge']:+6.1%}{mark}")
    print("\n  * clears the gate")

    if args.dry_run:
        print("\n  dry run — nothing written")
        return 0

    # record the single best candidate per market, flagged for whether it qualifies
    week = week_label(datetime.now(timezone.utc))
    with job("v2-picks") as jr, connect() as conn:
        conn.autocommit = False
        n = 0
        for market in ("cards", "draw"):
            best = next((c for c in candidates if c["market"] == market), None)
            if not best:
                continue
            leg = "leg2" if market == "cards" else "draw"
            cur = conn.execute("""
                insert into v1_live_record
                  (system, week, leg, market, match_text, pick, odds_quoted,
                   hit, match_id, kickoff_utc, issued_at, edge, qualifies,
                   model_prob, book_prob, note)
                values ('v2',%s,%s,%s,%s,%s,%s,null,%s,%s,now(),%s,%s,%s,%s,%s)
                on conflict (system, week, leg) do nothing""",
                (week, leg, "YC Over 3.5" if market == "cards" else "H/A",
                 best["match"], best["pick"], best["price"], best["match_id"],
                 best["kickoff"], best["edge"], best["edge"] >= args.min_edge,
                 best["model_prob"], best["book_prob"],
                 "v2 pick" + ("" if best["edge"] >= args.min_edge
                              else " — below edge gate, recorded for comparison only")))
            # count rows that actually landed, not attempts: an ON CONFLICT that
            # discards everything must not be able to report success
            n += cur.rowcount
        conn.commit()
        jr.add(n)
        print(f"\n  recorded {n} v2 picks for week {week}")
        if n == 0:
            raise RuntimeError(
                "v2 produced candidates but wrote no rows — every insert was "
                "discarded by ON CONFLICT. Do not treat this as a quiet week.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
