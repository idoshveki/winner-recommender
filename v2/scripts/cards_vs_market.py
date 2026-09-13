"""Do the card models beat Pinnacle's own cards line?

Uses the 149 matches for which we hold a real Pinnacle totals_cards quote,
at whatever line Pinnacle quoted - no ladder fit, no filtering on depth, which
is what biased the earlier cards backtest.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from v2.lib.jobs import connect
from v2.model import cards_models, counts, features
from v2.model.devig import devig_shin
from v2.model.experiment import edge_monotonicity, incremental_information
from v2.model.markets import pnl, settle_total
from v2.scripts.eval_cards_models import LINES, TRAIN_END, brier, load

THRESHOLDS = (0.00, 0.02, 0.05)


def main() -> int:
    with connect() as conn:
        rows = load(conn)
        quotes = defaultdict(dict)
        for mid, line, side, price in conn.execute("""
                select match_id, line, side, price from odds_snapshots
                where bookmaker='pinnacle' and market='totals_cards'"""):
            quotes[mid].setdefault(float(line), {})[side.lower()] = float(price)

    feats = features.build(rows)
    by_id = {f.match_id: f for f in feats}
    train = [f for f in feats if str(f.kickoff)[:10] < TRAIN_END]
    models = {
        "glm": counts.train(train, "target_cards_book", features.CARD_FEATURES, "cards"),
        "teamconv": cards_models.train_team_conv(train),
        "gbm": cards_models.train_line_gbm(train, LINES + (3.0, 4.0, 5.0)),
    }

    sample = []
    for mid, byline in quotes.items():
        row = by_id.get(mid)
        if row is None or not row.usable() or row.target_cards_book is None:
            continue
        two = {l: v for l, v in byline.items() if "over" in v and "under" in v}
        if not two:
            continue
        line = sorted(two)[len(two) // 2]          # the central quoted line
        v = two[line]
        sample.append(dict(row=row, line=line, over=v["over"], under=v["under"],
                           q=devig_shin(v["over"], v["under"]),
                           total=row.target_cards_book, date=str(row.kickoff)[:10]))
    sample.sort(key=lambda s: s["date"])
    print(f"{len(sample)} matches with a two-sided Pinnacle cards quote")
    print(f"lines used: {sorted({s['line'] for s in sample})}")
    print(f"date range: {sample[0]['date']} .. {sample[-1]['date']}\n")

    ys = [1 if s["total"] > s["line"] else 0 for s in sample]
    qs = [s["q"] for s in sample]
    print(f"market: mean p {np.mean(qs):.1%}, actual over-rate {np.mean(ys):.1%}, "
          f"brier {brier(qs, ys):.4f}\n")

    for name, model in models.items():
        ps, yy, qq, keep = [], [], [], []
        for s, y, q in zip(sample, ys, qs):
            p = model.p_over(s["row"], s["line"])
            if p is None:
                continue
            ps.append(p); yy.append(y); qq.append(q); keep.append(s)
        if len(ps) < 40:
            print(f"{name}: only {len(ps)} usable"); continue
        r = incremental_information(yy, qq, ps, name)
        print(r.line())

        bets = []
        for s, p in zip(keep, ps):
            for side, price, pp in (("over", s["over"], p), ("under", s["under"], 1 - p)):
                ev = pp * price - 1
                if ev > 0:
                    bets.append(dict(edge=ev, price=price,
                                     result=settle_total(s["total"], s["line"], side)))
                    break
        res = {}
        for t in THRESHOLDS:
            sel = [b for b in bets if b["edge"] >= t]
            res[t] = (len(sel), sum(pnl(b["result"], b["price"]) for b in sel) / len(sel)) \
                if sel else (0, 0.0)
        print("      " + "  ".join(
            f"@{t:.0%}: n={res[t][0]:3d} roi={res[t][1]:+7.2%}" for t in THRESHOLDS))
        ok, why = edge_monotonicity({t: res[t] for t in THRESHOLDS if res[t][0] > 0})
        print(f"      monotonicity: {'PASS' if ok else 'FAIL'} - {why}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
