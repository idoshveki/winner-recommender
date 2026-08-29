"""The pre-registered soft-vs-sharp experiment. See docs/PREREGISTRATION_LINESHOP.md

    python -m v2.scripts.run_lineshop
"""
from __future__ import annotations

import json
import random
from collections import defaultdict

import numpy as np

from v2.lib.config import V2
from v2.model.experiment import edge_monotonicity
from v2.model.lineshop import (THRESHOLDS, candidates_1x2, candidates_ah,
                               candidates_ou25)
from v2.model.markets import pnl

HOLDOUT_FROM = "2024-07-01"


def load():
    return json.loads((V2 / ".cache" / "football_data.json").read_text())


def integrity(rows) -> bool:
    bad = 0
    for r in rows:
        mx, b3, ps = r.get("max_close_h"), r.get("b365_close_h"), r.get("pinnacle_close_h")
        for other in (b3, ps):
            if mx and other and float(other) > float(mx) + 1e-6:
                bad += 1
    print(f"  integrity: {bad} rows where a book beats the market max "
          f"({'OK' if bad == 0 else 'COLUMN MAPPING ERROR'})")
    return bad == 0


def run(rows, name, fn, rng) -> dict:
    bets = [c for r in rows for c in fn(r)]
    if not bets:
        print(f"\n{name}: no bets"); return {}
    res = {}
    print(f"\n=== {name} ===")
    print(f"  {'thresh':>7s} {'n':>5s} {'ROI':>9s} {'95% CI':>20s} {'win%':>7s}")
    for t in THRESHOLDS:
        sel = [b for b in bets if b["edge"] >= t]
        settled = [b for b in sel if b["result"] is not None]
        if len(settled) < 20:
            res[t] = (0, 0.0); print(f"  {t:6.0%} {len(settled):5d}     (too few)"); continue
        p = [pnl(b["result"], b["price"]) for b in settled]
        roi = float(np.mean(p)); res[t] = (len(p), roi)
        sims = sorted(np.mean([rng.choice(p) for _ in p]) for _ in range(2000))
        wr = sum(1 for b in settled if b["result"] == 1.0) / len(settled)
        print(f"  {t:6.0%} {len(p):5d} {roi:+8.2%}  [{sims[50]:+.2%}, {sims[1950]:+.2%}] {wr:7.1%}")
    live = {t: v for t, v in res.items() if v[0] > 0}
    if len(live) >= 2:
        ok, why = edge_monotonicity(live)
        print(f"  monotonicity: {'PASS' if ok else 'FAIL'} - {why}")
    return res


def main() -> int:
    rows = [r for r in load() if r.get("home_goals") is not None]
    print(f"{len(rows)} matches\n")
    integrity(rows)
    explore = [r for r in rows if r["date"] < HOLDOUT_FROM]
    hold = [r for r in rows if r["date"] >= HOLDOUT_FROM]
    print(f"  exploratory {len(explore)} matches (< {HOLDOUT_FROM})")
    print(f"  HELD OUT    {len(hold)} matches")
    rng = random.Random(7)

    print("\n" + "=" * 66)
    print("NEGATIVE CONTROL - Pinnacle's own price vs its own fair value")
    print("must return about -(margin/2), i.e. -1% to -2%")
    print("=" * 66)
    for label, fn in (("CONTROL 1X2", lambda r: candidates_1x2(r, "pinnacle_close")),
                      ("CONTROL O/U", lambda r: candidates_ou25(
                          r, "pinnacle_close_over25", "pinnacle_close_under25"))):
        bets = [c for r in rows for c in fn(r) if c["result"] is not None]
        p_l = [pnl(b["result"], b["price"]) for b in bets]
        vig = np.mean([1 / b["price"] - b["p"] for b in bets])
        print(f"  {label}: n={len(p_l)}  ROI={np.mean(p_l):+.2%}  "
              f"(mean vig per selection {vig:+.2%})")

    for label, data in (("EXPLORATORY", explore), ("HELD OUT", hold)):
        print("\n" + "=" * 66)
        print(f"{label}  (n={len(data)})")
        print("=" * 66)
        print("\n--- ARM A: Bet365 CLOSING vs Pinnacle closing fair ---")
        run(data, "A 1X2", lambda r: candidates_1x2(r, "b365_close"), rng)
        run(data, "A O/U 2.5", lambda r: candidates_ou25(r, "b365_close_over25", "b365_close_under25"), rng)
        run(data, "A Asian handicap", lambda r: candidates_ah(r, "b365_close_ah_home", "b365_close_ah_away"), rng)

        print("\n--- ARM B (ACTIONABLE): Bet365 opening vs Pinnacle OPENING fair ---")
        print("    both prices exist at the same moment, so this is executable")
        run(data, "B 1X2", lambda r: candidates_1x2(r, "b365", "pinnacle_open"), rng)
        run(data, "B O/U 2.5", lambda r: candidates_ou25(
            r, "b365_open_over25", "b365_open_under25", "pinnacle_open"), rng)
        run(data, "B Asian handicap", lambda r: candidates_ah(
            r, "b365_open_ah_home", "b365_open_ah_away", "pinnacle_open", "ah_line"), rng)

        print("\n--- ARM C (DIAGNOSTIC ONLY, uses lookahead): Bet365 open vs Pinnacle CLOSE ---")
        print("    not executable - the closing line is unknown at bet time.")
        print("    reported because it measures how far soft openings sit from the close.")
        run(data, "C 1X2 (lookahead)", lambda r: candidates_1x2(r, "b365"), rng)
        run(data, "C AH (lookahead)", lambda r: candidates_ah(
            r, "b365_open_ah_home", "b365_open_ah_away"), rng)

        print("\n--- CONTEXT ONLY: best price anywhere (not accessible to you) ---")
        run(data, "MaxC 1X2", lambda r: candidates_1x2(r, "max_close"), rng)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
