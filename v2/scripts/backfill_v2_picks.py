"""What would v2 have picked, for the weeks v1 actually bet?

Writes to research.v2_backfill - an UNEXPOSED schema - because these are
backtest picks. v1 loaded 27 backtest weeks into its live table and its UI
reported them as a real 67% track record. Keeping these physically out of the
live table means that cannot happen again by accident.

Honest about what it is: features use only matches before each fixture, but
prices are CLOSING odds, which v1 could not have had on Friday. It answers
"would v2's selection have been better than v1's", not "here is a strategy you
could have run".

    python -m v2.scripts.backfill_v2_picks
"""
from __future__ import annotations

from collections import defaultdict

from v2.lib.jobs import connect, job
from v2.model import cards_models, features
from v2.model.devig import devig_shin
from v2.model.lineshop import devig_three_way
from v2.model.markets import settle_total
from v2.recommend.picks import MAX_DIVERGENCE, MIN_EDGE, MIN_HISTORY, load_history

CARD_LINES = (3.5, 4.5)


def main() -> int:
    with connect() as conn:
        hist = load_history(conn)
        weeks = [r[0] for r in conn.execute(
            "select distinct week from v1_live_record where system='v1' order by week")]
        cards_odds = defaultdict(dict)
        for mid, line, side, price in conn.execute("""
                select match_id, line, side, price from odds_snapshots
                where bookmaker='pinnacle' and market='totals_cards'"""):
            cards_odds[mid].setdefault(float(line), {})[side.lower()] = float(price)
        # football-data's Pinnacle feed carries NOTHING for 2026 fixtures - the
        # whole live era. Fall back to the market-average close, which is
        # complete for all 8,676. It carries more margin than Pinnacle, so the
        # de-vigged draw probability is a slightly softer benchmark; that is
        # stated rather than hidden.
        h2h = {r[0]: (float(r[1]), float(r[2]), float(r[3])) for r in conn.execute("""
            select match_id,
                   coalesce(pinnacle_close_h, avg_close_h),
                   coalesce(pinnacle_close_d, avg_close_d),
                   coalesce(pinnacle_close_a, avg_close_a)
            from historical_odds
            where coalesce(pinnacle_close_d, avg_close_d) is not null""")}
        stats = {r[0]: r[1:] for r in conn.execute("""
            select s.match_id, s.home_yellow+s.away_yellow+2*(s.home_red+s.away_red),
                   s.home_goals, s.away_goals
            from match_stats s where s.home_yellow is not null""")}

    feats = features.build(hist)
    by_id = {f.match_id: f for f in feats}
    print(f"  {len(weeks)} v1 weeks to match, {len(feats)} feature rows")

    with job("v2-backfill") as jr, connect() as conn:
        conn.autocommit = False
        written = 0
        for week in weeks:
            start = week.split("/")[0]
            fixtures = conn.execute("""
                select m.id, th.canonical_name||' vs '||ta.canonical_name, m.kickoff_utc
                from matches m
                join teams th on th.id=m.home_team_id join teams ta on ta.id=m.away_team_id
                where m.kickoff_utc::date between %s::date - 2 and %s::date + 13
                  and m.status='finished'""", (start, start)).fetchall()
            # train on everything strictly before this week
            train = [f for f in feats if str(f.kickoff)[:10] < start
                     and f.usable() and f.target_cards_book is not None]
            if len(train) < 500:
                continue
            gbm = cards_models.train_line_gbm(train, CARD_LINES)

            best = {}
            seen_h2h = 0
            for mid, text, ko in fixtures:
                row = by_id.get(mid)
                if row is None or not row.usable() or row.n_obs_min < MIN_HISTORY:
                    continue
                # cards
                two = {l: v for l, v in cards_odds.get(mid, {}).items()
                       if "over" in v and "under" in v}
                for line, v in two.items():
                    if line not in CARD_LINES:
                        continue
                    p = gbm.p_over(row, line)
                    if p is None or mid not in stats or stats[mid][0] is None:
                        continue
                    q = devig_shin(v["over"], v["under"])
                    for side, price, pp, qq in (("Over", v["over"], p, q),
                                                ("Under", v["under"], 1 - p, 1 - q)):
                        if abs(pp - qq) > MAX_DIVERGENCE:
                            continue
                        edge = pp * price - 1
                        if "cards" not in best or edge > best["cards"]["edge"]:
                            best["cards"] = dict(
                                market="cards", match=text, pick=f"{side} {line}",
                                match_id=mid, kickoff=ko, price=price, model=pp,
                                book=qq, edge=edge,
                                hit=(settle_total(stats[mid][0], line, side.lower()) == 1.0))
                # draws — v1's own gate, priced at Pinnacle's close
                if mid in h2h and mid in stats and stats[mid][1] is not None:
                    seen_h2h += 1
                    h, d, a = h2h[mid]
                    ph, pd_, pa = devig_three_way(h, d, a)
                    if pd_ >= 0.29:
                        edge = pd_ * d - 1
                        if "draw" not in best or edge > best["draw"]["edge"]:
                            hg, ag = stats[mid][1], stats[mid][2]
                            best["draw"] = dict(
                                market="draw", match=text, pick="D", match_id=mid,
                                kickoff=ko, price=d, model=pd_, book=pd_, edge=edge,
                                hit=(hg == ag))
            for mk, b in best.items():
                conn.execute("""
                    insert into research.v2_backfill
                      (week, market, match_text, pick, match_id, kickoff_utc, price,
                       model_prob, book_prob, edge, qualifies, hit)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (week, market) do nothing""",
                    (week, mk, b["match"], b["pick"], b["match_id"], b["kickoff"],
                     b["price"], b["model"], b["book"], b["edge"],
                     b["edge"] >= MIN_EDGE, b["hit"]))
                written += 1
            summary = ", ".join("%s:%s" % (k, v["pick"]) for k, v in best.items())
            print("    %s  %-28s (%d fixtures, %d with 1X2 odds)"
                  % (week, summary or "nothing", len(fixtures), seen_h2h))
        conn.commit()
        jr.add(written)
        print(f"\n  wrote {written} backfilled picks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
