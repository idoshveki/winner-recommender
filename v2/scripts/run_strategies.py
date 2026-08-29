"""Sweep systematic betting rules, with a permutation null over the whole sweep.

    python -m v2.scripts.run_strategies
"""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict

import numpy as np

from v2.lib.config import V2
from v2.model.markets import pnl
from v2.model.strategies import Rule, Selection, build_rules, selections

HOLDOUT_FROM = "2024-07-01"
MIN_BETS = 40


def roi_of(sels, results=None):
    """ROI for a list of selections. `results` overrides outcomes (for the null)."""
    tot, n = 0.0, 0
    for i, s in enumerate(sels):
        r = s.result if results is None else results[i]
        if r is None:
            continue
        tot += pnl(r, s.price); n += 1
    return (tot / n, n) if n else (0.0, 0)


def main() -> int:
    rows = json.loads((V2 / ".cache" / "football_data.json").read_text())
    all_sel = [s for r in rows for s in selections(r)]
    print(f"{len(rows)} matches -> {len(all_sel)} available bets at Pinnacle closing\n")

    counts = Counter()
    for r in rows:
        counts[r["home_team"]] += 1
    teams = [t for t, _ in counts.most_common(24)]
    rules = build_rules(teams)
    print(f"testing {len(rules)} pre-specified rules")
    print(f"  families: {dict(Counter(r.family for r in rules))}\n")

    explore = [s for s in all_sel if s.date < HOLDOUT_FROM]
    hold = [s for s in all_sel if s.date >= HOLDOUT_FROM]
    print(f"  exploratory {len({(s.date,s.home) for s in explore})} matches")
    print(f"  HELD OUT    {len({(s.date,s.home) for s in hold})} matches\n")

    def evaluate(pool, label):
        out = []
        for rule in rules:
            sel = [s for s in pool if rule.test(s)]
            roi, n = roi_of(sel)
            if n >= MIN_BETS:
                out.append((rule, n, roi, sel))
        out.sort(key=lambda x: -x[2])
        print(f"=== {label}: top 12 of {len(out)} rules with n>={MIN_BETS} ===")
        print(f"  {'rule':44s} {'n':>5s} {'ROI':>8s}")
        for rule, n, roi, _ in out[:12]:
            print(f"  {rule.name[:44]:44s} {n:5d} {roi:+7.2%}")
        print(f"  {'...worst 3':44s}")
        for rule, n, roi, _ in out[-3:]:
            print(f"  {rule.name[:44]:44s} {n:5d} {roi:+7.2%}")
        return out

    ex = evaluate(explore, "EXPLORATORY")
    print()
    ho = evaluate(hold, "HELD OUT")

    # ── the null that knows how many rules were tried ────────────────────
    print("\n" + "=" * 70)
    print("PERMUTATION NULL - if the market is exactly right, what does the")
    print(f"BEST of {len(ho)} rules look like by luck alone?")
    print("=" * 70)
    rng = random.Random(7)
    by_rule = {rule.name: sel for rule, n, roi, sel in ho}
    best_null = []
    for _ in range(600):
        # simulate every outcome from the market's own de-vigged probability
        sim = {id(s): (1.0 if rng.random() < s.p else 0.0) for s in hold}
        best = -9.0
        for rule, n, roi, sel in ho:
            tot = sum(pnl(sim[id(s)], s.price) for s in sel)
            best = max(best, tot / len(sel))
        best_null.append(best)
    best_null.sort()
    observed = ho[0][2] if ho else 0.0
    pct = sum(1 for x in best_null if x < observed) / len(best_null)
    print(f"  best rule under the null: median {np.median(best_null):+.2%}, "
          f"95th pct {best_null[int(.95*len(best_null))]:+.2%}")
    print(f"  our best observed rule:   {observed:+.2%}  ({ho[0][0].name})")
    print(f"  it sits at the {pct*100:.0f}th percentile of pure noise")
    print(f"\n  VERDICT: {'beats the null' if pct > 0.95 else 'INDISTINGUISHABLE from luck'}")

    # does anything survive from exploratory to held out?
    print("\n" + "=" * 70)
    print("DOES THE EXPLORATORY WINNER HOLD UP OUT OF SAMPLE?")
    print("=" * 70)
    ho_map = {rule.name: (n, roi) for rule, n, roi, _ in ho}
    print(f"  {'rule':44s} {'explore':>9s} {'held out':>10s}")
    for rule, n, roi, _ in ex[:8]:
        hn, hroi = ho_map.get(rule.name, (0, float('nan')))
        print(f"  {rule.name[:44]:44s} {roi:+8.2%} {hroi:+9.2%}" if hn
              else f"  {rule.name[:44]:44s} {roi:+8.2%} {'n/a':>9s}")
    corr = [(roi, ho_map[rule.name][1]) for rule, n, roi, _ in ex if rule.name in ho_map]
    if len(corr) > 5:
        a = np.array([c[0] for c in corr]); b = np.array([c[1] for c in corr])
        print(f"\n  correlation between exploratory ROI and held-out ROI: "
              f"{np.corrcoef(a, b)[0,1]:+.3f}  (n={len(corr)} rules)")
        print("  a real effect would give a strongly positive correlation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
