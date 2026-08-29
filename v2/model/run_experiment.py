"""Stage 4: the pre-registered analysis.

Gates, fixed in v2/docs/PREREGISTRATION.md before any odds were fetched:
  * primary  - out-of-sample money test on the hold-out, ROI positive at the
               2% threshold AND non-decreasing across {0%, 2%, 5%}
  * screening- incremental-information coefficient c (never a gate on its own)
  * integrity- fetched sample must match the population it was drawn from

    python -m v2.model.run_experiment
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import statsmodels.api as sm

from v2.lib.jobs import connect
from v2.model import counts, diffmodel, features
from v2.model.devig import devig_shin
from v2.model.experiment import (
    benjamini_hochberg, edge_monotonicity, incremental_information, logit,
    minimum_detectable_c,
)
from v2.model.markets import pnl, settle_handicap, settle_total

TRAIN_END = "2025-11-30"
THRESHOLDS = (0.00, 0.02, 0.05)


def load(conn):
    rows = [
        dict(match_id=r[0], league=r[1], kickoff_utc=r[2], home_team_id=r[3],
             away_team_id=r[4], home_yellow=r[5], away_yellow=r[6],
             home_corners=r[7], away_corners=r[8], home_shots=r[9], away_shots=r[10],
             home_fouls=r[11], away_fouls=r[12], home_goals=r[13], away_goals=r[14],
             odds_h=r[15], odds_d=r[16], odds_a=r[17], home_name=r[18], away_name=r[19])
        for r in conn.execute("""
            select m.id, m.league, m.kickoff_utc, m.home_team_id, m.away_team_id,
                   s.home_yellow, s.away_yellow, s.home_corners, s.away_corners,
                   s.home_shots, s.away_shots, s.home_fouls, s.away_fouls,
                   s.home_goals, s.away_goals,
                   coalesce(o.pinnacle_close_h, o.avg_h),
                   coalesce(o.pinnacle_close_d, o.avg_d),
                   coalesce(o.pinnacle_close_a, o.avg_a),
                   th.canonical_name, ta.canonical_name
            from matches m
            join match_stats s on s.match_id = m.id
            join teams th on th.id = m.home_team_id
            join teams ta on ta.id = m.away_team_id
            left join historical_odds o on o.match_id = m.id
            where m.status='finished'
            order by m.kickoff_utc, m.id""")
    ]
    quotes: Dict[Tuple[int, str], List[tuple]] = defaultdict(list)
    for mid, market, line, side, price in conn.execute("""
            select match_id, market, line, side, price from odds_snapshots
            where bookmaker='pinnacle' and fetched_at < now() - interval '60 days'
              and market in ('spreads_cards','totals_corners')"""):
        quotes[(mid, market)].append((float(line), side, float(price)))
    return rows, quotes


def integrity(rows, quotes, window_from: str) -> bool:
    """The fetched sample must look like the population it was drawn from.
    This is the check that would have caught the 3.12-vs-4.14 May artefact."""
    pop = [r for r in rows if str(r["kickoff_utc"])[:10] >= window_from
           and r["home_yellow"] is not None]
    ids = {mid for (mid, _) in quotes}
    smp = [r for r in pop if r["match_id"] in ids]
    if not smp:
        print("  integrity: NO SAMPLE"); return False
    ok = True
    for label, key in (("cards", lambda r: r["home_yellow"] + r["away_yellow"]),
                       ("corners", lambda r: (r["home_corners"] or 0) + (r["away_corners"] or 0))):
        mp, ms = np.mean([key(r) for r in pop]), np.mean([key(r) for r in smp])
        flag = "OK" if abs(mp - ms) <= 0.25 else "BIASED"
        if flag == "BIASED":
            ok = False
        print(f"  integrity {label:8s} population {mp:5.2f}  sample {ms:5.2f}  "
              f"diff {ms-mp:+5.2f}  [{flag}]")
    print(f"  sample size: {len(smp)} of {len(pop)} matches in window")
    return ok


def money_test(bets: List[dict]) -> Dict[float, Tuple[int, float]]:
    out = {}
    for t in THRESHOLDS:
        sel = [b for b in bets if b["edge"] >= t]
        if not sel:
            out[t] = (0, 0.0); continue
        profit = sum(pnl(b["result"], b["price"]) for b in sel)
        out[t] = (len(sel), profit / len(sel))
    return out


def evaluate(name, hypothesis, samples, conn=None):
    """samples: list of dicts with y, q, p, price_for_side, result, date."""
    samples = sorted(samples, key=lambda s: s["date"])
    if len(samples) < 60:
        print(f"\n{name}: only {len(samples)} usable matches - too few to test")
        return None
    cal, hold = samples[:len(samples) // 2], samples[len(samples) // 2:]
    print(f"\n=== {name} ===")
    print(f"  {hypothesis}")
    print(f"  calibration {len(cal)} ({cal[0]['date']}..{cal[-1]['date']})  "
          f"hold-out {len(hold)} ({hold[0]['date']}..{hold[-1]['date']})")

    lq = np.array([logit(s["q"]) for s in cal]); lp = np.array([logit(s["p"]) for s in cal])
    X = sm.add_constant(np.column_stack([lq, lp - lq]), has_constant="add")
    fit = sm.Logit(np.array([s["y"] for s in cal], dtype=float), X).fit(disp=0)
    print(f"  blend on calibration: b={fit.params[1]:+.3f} c={fit.params[2]:+.3f} "
          f"(p={fit.pvalues[2]:.3f})")

    screen = incremental_information([s["y"] for s in hold], [s["q"] for s in hold],
                                     [s["p"] for s in hold], name)
    print("  screening " + screen.line().strip())

    bets = []
    for s in hold:
        a, b = logit(s["q"]), logit(s["p"])
        pb = 1 / (1 + np.exp(-(fit.params[0] + fit.params[1] * a + fit.params[2] * (b - a))))
        ev = pb * s["price"] - 1
        if ev > 0:
            bets.append(dict(edge=ev, price=s["price"], result=s["result"]))
    res = money_test(bets)
    print(f"  {'threshold':>10s} {'n':>5s} {'ROI':>9s}")
    for t in THRESHOLDS:
        n, roi = res[t]
        print(f"  {t:9.0%} {n:5d} {roi:+8.2%}" if n else f"  {t:9.0%} {0:5d}        -")
    mono_ok, why = edge_monotonicity({t: res[t] for t in THRESHOLDS if res[t][0] > 0})
    roi2 = res[0.02][1] if res[0.02][0] else -1
    passed = mono_ok and roi2 > 0
    print(f"  monotonicity: {why}")
    print(f"  GATE: {'PASS' if passed else 'FAIL'}")
    return screen, res, passed


def main() -> int:
    ap = argparse.ArgumentParser(); ap.parse_args()
    with connect() as conn:
        rows, quotes = load(conn)
    print(f"loaded {len(rows)} matches, {len(quotes)} priced match-markets\n")
    print("SAMPLE INTEGRITY")
    if not integrity(rows, quotes, "2025-12-01"):
        print("\n  sample is biased - per the pre-registration this run is VOID")
        return 1

    feats = features.build(rows)
    by_id = {f.match_id: f for f in feats}
    train = [f for f in feats if str(f.kickoff)[:10] < TRAIN_END]
    print(f"\ntraining on {len(train)} matches before {TRAIN_END}")
    print(f"minimum detectable c at n~600: {minimum_detectable_c(600):.3f}")

    corner_model = counts.train(train, "target_corners", features.CORNER_FEATURES,
                                "totals_corners")
    card_model = diffmodel.train(train, features.CARD_DIFF_FEATURES)
    print(f"  corners model leagues: {sorted(corner_model.by_league)}")
    print(f"  cards-diff model leagues: {sorted(card_model.by_league)}")

    results = []

    # ---- H2: corners totals -------------------------------------------
    S = []
    for (mid, market), qs in quotes.items():
        if market != "totals_corners":
            continue
        row = by_id.get(mid)
        if row is None or not row.usable() or row.target_corners is None:
            continue
        by_line = defaultdict(dict)
        for line, side, price in qs:
            by_line[line][side.lower()] = price
        two = {l: v for l, v in by_line.items() if "over" in v and "under" in v}
        if not two:
            continue
        line = min(two, key=lambda l: abs(l - np.median(list(two))))
        v = two[line]
        q = devig_shin(v["over"], v["under"])
        p = corner_model.p_over(row, line)
        if p is None:
            continue
        side = "over" if p > q else "under"
        S.append(dict(y=1 if row.target_corners > line else 0,
                      q=q if side == "over" else 1 - q,
                      p=p if side == "over" else 1 - p,
                      price=v[side],
                      result=settle_total(row.target_corners, line, side),
                      date=str(row.kickoff)[:10]))
    r = evaluate("H2 corners totals", "shots/corner-share beat the closing total", S)
    if r: results.append(r)

    # ---- H1: cards handicap -------------------------------------------
    name_by_id = {r_["match_id"]: (r_["home_name"], r_["away_name"], r_) for r_ in rows}
    S = []
    for (mid, market), qs in quotes.items():
        if market != "spreads_cards" or mid not in name_by_id:
            continue
        home, away, raw = name_by_id[mid]
        row = by_id.get(mid)
        if row is None or not row.usable() or raw["home_yellow"] is None:
            continue
        sides = {}
        for line, side, price in qs:
            which = "home" if side == home else ("away" if side == away else None)
            if which:
                sides[which] = (line, price)
        if len(sides) != 2:
            continue
        ph = card_model.p_cover(row, sides["home"][0], "home")
        if ph is None:
            continue
        q_raw_h = 1 / sides["home"][1]
        q_raw_a = 1 / sides["away"][1]
        qh = q_raw_h / (q_raw_h + q_raw_a)
        pick = "home" if ph > qh else "away"
        point, price = sides[pick]
        own, opp = ((raw["home_yellow"], raw["away_yellow"]) if pick == "home"
                    else (raw["away_yellow"], raw["home_yellow"]))
        S.append(dict(y=1 if settle_handicap(own, opp, point) == 1.0 else 0,
                      q=qh if pick == "home" else 1 - qh,
                      p=ph if pick == "home" else 1 - ph,
                      price=price,
                      result=settle_handicap(own, opp, point),
                      date=str(row.kickoff)[:10]))
    r = evaluate("H1 cards handicap", "fouls beat the closing cards handicap", S)
    if r: results.append(r)

    if results:
        adj = benjamini_hochberg([x[0] for x in results], alpha=0.10)
        print("\n=== FDR-adjusted screening (Benjamini-Hochberg, alpha=0.10) ===")
        for a in adj:
            print(a.line())
        print("\n=== VERDICT ===")
        for (screen, _, passed) in results:
            print(f"  {screen.market:24s} {'PASSES the money gate' if passed else 'fails'}")
        if not any(p for (_, _, p) in results):
            print("\n  No market passed. Per the pre-registered stopping rule, the")
            print("  model-driven approach is finished. No re-running with different")
            print("  features or windows in search of a pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
