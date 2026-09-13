"""Scan the feature mart for high-hit-rate rules.

Rules are of the shape the user asked for: a combined predictor
("home venue card average + away venue card average") crossed with context
(league, round, position, form streak), and an outcome.

Break-even at the odds being targeted:
    1.50 -> 66.7%     1.55 -> 64.5%     1.60 -> 62.5%

Every scan is paired with a permutation null. Given a rule set this large,
SOMETHING will clear 70% by luck; the null says how high a bar that really is.

    python -m v2.scripts.scan_rules
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from typing import Callable, List, Optional

import numpy as np

from v2.lib.jobs import connect

HOLDOUT_FROM = "2024-07-01"
MIN_N = 120
BREAKEVEN = {1.50: 0.667, 1.55: 0.645, 1.60: 0.625}


@dataclass
class Outcome:
    name: str
    fn: Callable[[dict], Optional[bool]]


OUTCOMES = [
    Outcome("cards over 3.5", lambda r: r["total_cards_book"] > 3.5 if r["total_cards_book"] is not None else None),
    Outcome("cards over 4.5", lambda r: r["total_cards_book"] > 4.5 if r["total_cards_book"] is not None else None),
    Outcome("cards under 5.5", lambda r: r["total_cards_book"] < 5.5 if r["total_cards_book"] is not None else None),
    Outcome("corners over 8.5", lambda r: r["total_corners"] > 8.5 if r["total_corners"] is not None else None),
    Outcome("corners over 9.5", lambda r: r["total_corners"] > 9.5 if r["total_corners"] is not None else None),
    Outcome("corners under 11.5", lambda r: r["total_corners"] < 11.5 if r["total_corners"] is not None else None),
    Outcome("goals over 1.5", lambda r: r["total_goals"] > 1.5),
    Outcome("goals under 3.5", lambda r: r["total_goals"] < 3.5),
    Outcome("BTTS", lambda r: r["btts"]),
    Outcome("home or draw", lambda r: r["result"] in ("H", "D")),
    Outcome("not away win", lambda r: r["result"] != "A"),
]


def build_filters():
    """Pre-specified filter grid. Named so results are readable."""
    F = []
    def add(name, fn): F.append((name, fn))
    add("all matches", lambda r: True)
    for lg in ("EPL", "La_Liga", "Serie_A", "Bundesliga"):
        add(f"{lg}", lambda r, lg=lg: r["league"] == lg)
    for t in (4.0, 4.5, 5.0, 5.5, 6.0):
        add(f"cards_pred>={t}", lambda r, t=t: r["cards_pred"] is not None and float(r["cards_pred"]) >= t)
    for t in (3.0, 3.5, 4.0):
        add(f"cards_pred<={t}", lambda r, t=t: r["cards_pred"] is not None and float(r["cards_pred"]) <= t)
    for t in (9.0, 10.0, 11.0, 12.0):
        add(f"corners_pred>={t}", lambda r, t=t: r["corners_pred"] is not None and float(r["corners_pred"]) >= t)
    for t in (2.5, 3.0, 3.5):
        add(f"goals_pred>={t}", lambda r, t=t: r["goals_pred"] is not None and float(r["goals_pred"]) >= t)
    add("early season (rd<=10)", lambda r: r["round_num"] <= 10)
    add("mid season (rd 11-27)", lambda r: 11 <= r["round_num"] <= 27)
    add("late season (rd>=28)", lambda r: r["round_num"] >= 28)
    add("both top 6", lambda r: r["both_top6"])
    add("both bottom 6", lambda r: r["both_bottom6"])
    add("big mismatch (|pos diff|>=10)", lambda r: r["position_diff"] is not None and abs(r["position_diff"]) >= 10)
    add("close in table (|pos diff|<=3)", lambda r: r["position_diff"] is not None and abs(r["position_diff"]) <= 3)
    add("home in form (l5>=10)", lambda r: r["form_diff"] is not None and r["home_l5_points"] >= 10)
    add("home out of form (l5<=4)", lambda r: r["home_l5_points"] <= 4)
    add("home on win streak>=3", lambda r: r["home_win_streak"] >= 3)
    add("away winless>=3", lambda r: r["away_winless_streak"] >= 3)
    return F


def load(conn):
    return [dict(zip([d.name for d in cur.description], row)) for cur, row in
            [(c, r) for c in [conn.execute("""
                select f.*, hf.l5_points as home_l5_points, hf.win_streak as home_win_streak,
                       af.winless_streak as away_winless_streak
                from match_features f
                join team_match_form hf on hf.match_id=f.match_id and hf.team_id=f.home_team_id
                join team_match_form af on af.match_id=f.match_id and af.team_id=f.away_team_id
                order by f.kickoff_utc""")] for r in c]]


def main() -> int:
    with connect() as conn:
        rows = load(conn)
    print(f"{len(rows)} matches in the mart")
    explore = [r for r in rows if r["kickoff_utc"].date().isoformat() < HOLDOUT_FROM]
    hold = [r for r in rows if r["kickoff_utc"].date().isoformat() >= HOLDOUT_FROM]
    print(f"  exploratory {len(explore)}   HELD OUT {len(hold)}\n")

    filters = build_filters()
    # single filters and pairs, which is where "complex correlations" live
    combos = [(n, [f]) for n, f in filters]
    for (n1, f1), (n2, f2) in itertools.combinations(filters[1:], 2):
        combos.append((f"{n1} AND {n2}", [f1, f2]))
    print(f"{len(combos)} filter combinations x {len(OUTCOMES)} outcomes "
          f"= {len(combos)*len(OUTCOMES)} rules\n")

    def hit(pool, fs, oc):
        ys = []
        for r in pool:
            try:
                if not all(f(r) for f in fs):
                    continue
                v = oc.fn(r)
            except Exception:
                continue
            if v is not None:
                ys.append(1 if v else 0)
        return ys

    results = []
    for name, fs in combos:
        for oc in OUTCOMES:
            ys = hit(explore, fs, oc)
            if len(ys) < MIN_N:
                continue
            results.append((np.mean(ys), len(ys), name, oc.name, fs, oc))
    results.sort(reverse=True)
    print(f"=== EXPLORATORY: top 15 by hit rate (n>={MIN_N}) ===")
    print(f"  {'rate':>6s} {'n':>5s}  {'outcome':18s} {'filter'}")
    for rate, n, name, ocn, _, _ in results[:15]:
        print(f"  {rate:6.1%} {n:5d}  {ocn:18s} {name}")

    print(f"\n=== the same rules on HELD-OUT data ===")
    print(f"  {'explore':>8s} {'held':>7s} {'n':>5s}  {'outcome':18s} {'filter'}")
    kept = []
    for rate, n, name, ocn, fs, oc in results[:15]:
        ys = hit(hold, fs, oc)
        if len(ys) < 40:
            print(f"  {rate:7.1%} {'n/a':>7s} {len(ys):5d}  {ocn:18s} {name}")
            continue
        kept.append((np.mean(ys), rate))
        print(f"  {rate:7.1%} {np.mean(ys):6.1%} {len(ys):5d}  {ocn:18s} {name}")
    if len(kept) > 3:
        a = np.array([k[1] for k in kept]); b = np.array([k[0] for k in kept])
        print(f"\n  mean drop from exploratory to held out: {np.mean(a-b)*100:+.1f} points")

    print("\n" + "=" * 66)
    print(f"PERMUTATION NULL over {len(results)} rules")
    print("=" * 66)
    rng = random.Random(7)
    base = {oc.name: np.mean([v for v in (oc.fn(r) for r in explore) if v is not None])
            for oc in OUTCOMES}
    best_null = []
    for _ in range(300):
        best = 0.0
        for rate, n, name, ocn, _, _ in results:
            p = base[ocn]
            sim = sum(1 for _ in range(n) if rng.random() < p) / n
            best = max(best, sim)
        best_null.append(best)
    best_null.sort()
    print(f"  if every rule were pure noise at its outcome's base rate,")
    print(f"  the BEST of {len(results)} rules would hit "
          f"{np.median(best_null):.1%} (median), {best_null[int(.95*len(best_null))]:.1%} (95th pct)")
    print(f"  our best observed: {results[0][0]:.1%}")
    print(f"\n  break-even needed: 1.50 -> 66.7%   1.55 -> 64.5%   1.60 -> 62.5%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
