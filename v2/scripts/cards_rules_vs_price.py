"""Do the card rules make money at the prices actually charged?

The rules lift the hit rate well above the 61.5% base (68-75% out of sample).
The open question is whether the market charges more for exactly those matches -
because the matches a "lots of cards" rule selects are the ones a book prices
shortest.

Settles two different markets, which are NOT the same quantity:
  * Pinnacle's totals_cards - yellows + 2x reds, priced at its own quote
  * 1win's "Yellow cards"   - yellows only, priced from Pinnacle converted
                              (mean minus 2x league reds) plus 1win's 8.4% margin

    python -m v2.scripts.cards_rules_vs_price
"""
from __future__ import annotations

import random
from collections import defaultdict

import numpy as np

from v2.lib.jobs import connect
from v2.model.devig import devig_shin, p_over_from_dist
from v2.model.markets import pnl, settle_total
from v2.model.onewin import ONEWIN_MARGIN, yellows_mean

F = lambda r, k: float(r[k]) if r.get(k) is not None else None

RULES = {
    "ALL La Liga (baseline)":            lambda r: True,
    "cards_pred >= 4.5":                 lambda r: F(r,"cards_pred") and F(r,"cards_pred") >= 4.5,
    "cards_pred >= 5.0":                 lambda r: F(r,"cards_pred") and F(r,"cards_pred") >= 5.0,
    "cards_pred >= 5.5":                 lambda r: F(r,"cards_pred") and F(r,"cards_pred") >= 5.5,
    "both poor form (l5<=5 each)":       lambda r: r["h_l5"] <= 5 and r["a_l5"] <= 5,
    "cards_pred>=4.5 + both poor form":  lambda r: F(r,"cards_pred") and F(r,"cards_pred")>=4.5 and r["h_l5"]<=5 and r["a_l5"]<=5,
    "cards_pred>=5.0 + both poor form":  lambda r: F(r,"cards_pred") and F(r,"cards_pred")>=5.0 and r["h_l5"]<=5 and r["a_l5"]<=5,
    "both bottom 6":                     lambda r: r["both_bottom6"],
    "fouls_pred >= 28":                  lambda r: F(r,"fouls_pred") and F(r,"fouls_pred") >= 28,
    "fouls_pred>=28 + tight table":      lambda r: F(r,"fouls_pred") and F(r,"fouls_pred")>=28 and r["position_diff"] is not None and abs(r["position_diff"])<=3,
    "tight table (|pos|<=3)":            lambda r: r["position_diff"] is not None and abs(r["position_diff"]) <= 3,
}


def infer_book_mean(line: float, p_over: float) -> float:
    lo, hi = 0.5, 12.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if p_over_from_dist(mid, 55.0, line) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def main() -> int:
    with connect() as conn:
        lad = defaultdict(dict)
        for mid, line, side, price in conn.execute("""
                select match_id, line, side, price from odds_snapshots
                where bookmaker='pinnacle' and market='totals_cards'"""):
            lad[mid].setdefault(float(line), {})[side.lower()] = float(price)
        cur = conn.execute("""
            select f.match_id, f.league, f.kickoff_utc::date as d, f.cards_pred, f.fouls_pred,
                   f.position_diff, f.both_bottom6, f.total_cards_book, f.total_yellows,
                   hf.l5_points as h_l5, af.l5_points as a_l5
            from match_features f
            join team_match_form hf on hf.match_id=f.match_id and hf.team_id=f.home_team_id
            join team_match_form af on af.match_id=f.match_id and af.team_id=f.away_team_id
            where f.match_id = any(%s) and f.league='La_Liga'""", (list(lad),))
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur]

    print(f"{len(rows)} La Liga matches with a real Pinnacle cards quote")
    if rows:
        print(f"  {min(r['d'] for r in rows)} .. {max(r['d'] for r in rows)}\n")

    rng = random.Random(7)
    print(f"{'rule':34s} {'n':>4s} | {'PINNACLE (cards incl reds)':^30s} | "
          f"{'1WIN (yellows only)':^30s}")
    print(f"{'':34s} {'':>4s} | {'hit':>6s} {'price':>6s} {'ROI':>8s} {'95% CI':>7s} | "
          f"{'hit':>6s} {'price':>6s} {'ROI':>8s} {'95% CI':>7s}")
    for name, fn in RULES.items():
        sel = [r for r in rows if fn(r)]
        pin, win = [], []
        for r in sel:
            two = {l: v for l, v in lad[r["match_id"]].items() if "over" in v and "under" in v}
            if not two:
                continue
            line = sorted(two)[len(two) // 2]
            v = two[line]
            p_over = devig_shin(v["over"], v["under"])
            # Pinnacle's own market, at its own price
            res = settle_total(r["total_cards_book"], line, "over")
            if res is not None:
                pin.append((pnl(res, v["over"]), 1 if res == 1.0 else 0, v["over"]))
            # 1win: yellows only at line 3.5, priced from the converted mean
            mu_y = yellows_mean(infer_book_mean(line, p_over), "La_Liga")
            p35 = p_over_from_dist(mu_y, 55.0, 3.5)
            price = 1.0 / (p35 * (1 + ONEWIN_MARGIN))
            res2 = settle_total(r["total_yellows"], 3.5, "over")
            if res2 is not None:
                win.append((pnl(res2, price), 1 if res2 == 1.0 else 0, price))
        if len(pin) < 25:
            print(f"{name[:34]:34s} {len(pin):4d} | (too few)")
            continue

        def block(rec):
            p = [x[0] for x in rec]
            roi = float(np.mean(p))
            sims = sorted(np.mean([rng.choice(p) for _ in p]) for _ in range(1500))
            return (np.mean([x[1] for x in rec]), np.mean([x[2] for x in rec]),
                    roi, sims[37], sims[1462])
        h1, pr1, roi1, lo1, hi1 = block(pin)
        h2, pr2, roi2, lo2, hi2 = block(win)
        flag = " *" if lo2 > 0 else ""
        print(f"{name[:34]:34s} {len(pin):4d} | {h1:5.1%} {pr1:6.2f} {roi1:+7.1%} "
              f"[{lo1:+.0%},{hi1:+.0%}] | {h2:5.1%} {pr2:6.2f} {roi2:+7.1%} "
              f"[{lo2:+.0%},{hi2:+.0%}]{flag}")
    print("\n  * = 95% CI excludes zero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
