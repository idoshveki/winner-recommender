"""The yellows-only model against 1win's (predicted) prices.

1win's price is derived from Pinnacle's, so this is really "our model vs
Pinnacle, handicapped by 1win's extra margin". Stating that plainly because it
bounds what the test can show: if we do not beat Pinnacle, we cannot beat a
wider-margin copy of Pinnacle.

    CARD_TARGET=target_cards python -m v2.scripts.yellows_vs_1win
"""
from __future__ import annotations

import random
from collections import defaultdict

import numpy as np

from v2.lib.jobs import connect
from v2.model import cards_models, features
from v2.model.devig import devig_shin, fit_ladder, p_over_from_dist
from v2.model.experiment import edge_monotonicity
from v2.model.markets import pnl, settle_total
from v2.model.onewin import LEAGUE_REDS, ONEWIN_MARGIN, predict_prices, yellows_mean
from v2.scripts.eval_cards_models import LINES, TRAIN_END, brier, load

THRESHOLDS = (0.00, 0.02, 0.05)


def main() -> int:
    with connect() as conn:
        rows = load(conn)
        lad = defaultdict(dict)
        for mid, line, side, price in conn.execute("""
                select match_id, line, side, price from odds_snapshots
                where bookmaker='pinnacle' and market='totals_cards'"""):
            lad[mid].setdefault(float(line), {})[side.lower()] = float(price)

    feats = features.build(rows)
    by_id = {f.match_id: f for f in feats}
    train = [f for f in feats if str(f.kickoff)[:10] < TRAIN_END]
    gbm = cards_models.train_line_gbm(train, LINES + (3.0, 4.0, 5.0))

    bets, cal = [], []
    for mid, byline in lad.items():
        row = by_id.get(mid)
        if row is None or not row.usable() or row.target_cards is None:
            continue
        two = {l: v for l, v in byline.items() if "over" in v and "under" in v}
        if not two:
            continue
        fit = fit_ladder([(l, devig_shin(v["over"], v["under"])) for l, v in sorted(two.items())]) \
            if len(two) >= 2 else None
        if fit is None:
            # Single quoted line: solve for the mean that reproduces the
            # de-vigged probability exactly, rather than approximating it.
            # 124 of 149 matches land here, so a crude fit would dominate.
            line0 = list(two)[0]; v = two[line0]
            target = devig_shin(v["over"], v["under"])
            disp = 55.0
            lo, hi = 0.5, 12.0
            for _ in range(60):
                mid = (lo + hi) / 2
                if p_over_from_dist(mid, disp, line0) < target:
                    lo = mid
                else:
                    hi = mid
            mu_book = (lo + hi) / 2
        else:
            mu_book, disp = fit.mu, fit.dispersion
        for line in (2.5, 3.5, 4.5):
            p = gbm.p_over(row, line)
            if p is None:
                continue
            o_price, u_price = predict_prices(mu_book, row.league, line, disp)
            mu_y = yellows_mean(mu_book, row.league)
            q = p_over_from_dist(mu_y, disp, line)
            cal.append((p, q, 1 if row.target_cards > line else 0))
            for side, price, pp in (("over", o_price, p), ("under", u_price, 1 - p)):
                ev = pp * price - 1
                if ev > 0:
                    bets.append(dict(edge=ev, price=price, line=line,
                                     result=settle_total(row.target_cards, line, side),
                                     side=side))
                    break

    ps = [c[0] for c in cal]; qs = [c[1] for c in cal]; ys = [c[2] for c in cal]
    print(f"{len(cal)} match-lines from {len({b['line'] for b in bets})} lines\n")
    print(f"  model  mean p {np.mean(ps):.1%}  brier {brier(ps, ys):.4f}")
    print(f"  market mean p {np.mean(qs):.1%}  brier {brier(qs, ys):.4f}   "
          f"(Pinnacle, converted to yellows)")
    print(f"  actual over-rate {np.mean(ys):.1%}\n")

    print(f"  {'threshold':>10s} {'n':>5s} {'ROI':>9s} {'95% CI':>20s}")
    rng = random.Random(7); res = {}
    for t in THRESHOLDS:
        sel = [b for b in bets if b["edge"] >= t]
        if not sel:
            res[t] = (0, 0.0); print(f"  {t:9.0%} {0:5d}         -"); continue
        p = [pnl(b["result"], b["price"]) for b in sel]
        roi = float(np.mean(p)); res[t] = (len(p), roi)
        sims = sorted(np.mean([rng.choice(p) for _ in p]) for _ in range(4000))
        print(f"  {t:9.0%} {len(p):5d} {roi:+8.2%}  [{sims[100]:+.1%}, {sims[3900]:+.1%}]")
    ok, why = edge_monotonicity({t: res[t] for t in THRESHOLDS if res[t][0] > 0})
    print(f"\n  monotonicity: {'PASS' if ok else 'FAIL'} - {why}")
    print(f"  GATE: {'PASS' if ok and res[0.02][1] > 0 else 'FAIL'}")
    u = sum(1 for b in bets if b["side"] == "under")
    print(f"  side split: {u}/{len(bets)} under ({u/len(bets):.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
