"""Stage 0: validate the experiment harness before spending a single credit.

Two controls, both required:

  NEGATIVE - our model vs the closing over/under 2.5 goals line, on 8,676
    matches, using odds already in the local cache. Goals totals is the most
    efficiently priced market in football. Expect c ~ 0. If the harness finds
    an edge here, the harness is wrong and nothing downstream means anything.

  POSITIVE - the same test with a probability that genuinely does contain
    extra information (a faint peek at the outcome). Expect c > 0. A harness
    that never detects anything is just as broken as one that invents edges.

    python -m v2.scripts.stage0_control
"""
from __future__ import annotations

import json
import random

import numpy as np

from v2.lib.config import V2
from v2.model import counts, features
from v2.model.devig import devig_shin
from v2.model.experiment import incremental_information, minimum_detectable_c

CACHE = V2 / ".cache" / "football_data.json"
TRAIN_END = "2025-06-30"


def main() -> int:
    rows = json.loads(CACHE.read_text())
    # team ids: names are fine as keys for the rolling stores
    for r in rows:
        r["match_id"] = f"{r['league']}|{r['date']}|{r['home_team']}|{r['away_team']}"
        r["home_team_id"] = f"{r['league']}|{r['home_team']}"
        r["away_team_id"] = f"{r['league']}|{r['away_team']}"
        r["kickoff_utc"] = r["date"]
        r["odds_h"] = r.get("pinnacle_close_h") or r.get("avg_h")
        r["odds_d"] = r.get("pinnacle_close_d") or r.get("avg_d")
        r["odds_a"] = r.get("pinnacle_close_a") or r.get("avg_a")
    rows.sort(key=lambda r: (r["date"], r["home_team"]))
    feats = features.build(rows)
    by_id = {f.match_id: f for f in feats}
    print(f"built features for {len(feats)} matches")

    train = [f for f in feats if str(f.kickoff)[:10] < TRAIN_END]
    model = counts.train(train, "target_goals", features.GOAL_FEATURES, "totals_goals")
    print(f"trained on {len(train)} matches before {TRAIN_END}: "
          + ", ".join(f"{lg}:n={lm.n_train}" for lg, lm in sorted(model.by_league.items())))

    y, q, p = [], [], []
    skipped = 0
    for r in rows:
        if str(r["date"])[:10] < TRAIN_END:
            continue
        # CLOSING price. Pinnacle preferred, market-average as fallback.
        over = r.get("pinnacle_close_over25") or r.get("avg_close_over25")
        under = r.get("pinnacle_close_under25") or r.get("avg_close_under25")
        row = by_id.get(r["match_id"])
        if not over or not under or row is None or not row.usable():
            skipped += 1
            continue
        if row.target_goals is None:
            skipped += 1
            continue
        pm = model.p_over(row, 2.5)
        if pm is None:
            skipped += 1
            continue
        y.append(1 if row.target_goals > 2.5 else 0)
        q.append(devig_shin(float(over), float(under)))
        p.append(pm)

    print(f"test sample: {len(y)} matches (skipped {skipped})")
    print(f"minimum detectable c at this n: ~{minimum_detectable_c(len(y)):.3f}\n")

    neg = incremental_information(y, q, p, "CONTROL goals O2.5")
    print("NEGATIVE control - expect c ~ 0 (efficient market):")
    print(neg.line())
    print(f"    market base rate {neg.base_rate:.1%}, mean market p {np.mean(q):.1%}, "
          f"mean model p {np.mean(p):.1%}, b={neg.b:.3f}")

    # positive control: nudge the model probability toward the truth
    rng = random.Random(11)
    p_peek = [min(0.98, max(0.02, pi + (0.10 if yi else -0.10) + rng.gauss(0, 0.05)))
              for pi, yi in zip(p, y)]
    pos = incremental_information(y, q, p_peek, "CONTROL model+peek")
    print("\nPOSITIVE control - expect c > 0 (this probability really does know more):")
    print(pos.line())

    print("\n=== GATE ===")
    ok_neg = neg.c_p >= 0.05 or neg.c <= 0
    ok_pos = pos.c > 0 and pos.c_p < 0.01
    print(f"  negative control null?      {'PASS' if ok_neg else 'FAIL'} "
          f"(c={neg.c:+.3f}, p={neg.c_p:.3f})")
    print(f"  positive control detected?  {'PASS' if ok_pos else 'FAIL'} "
          f"(c={pos.c:+.3f}, p={pos.c_p:.3g})")
    print("  -> harness is trustworthy" if (ok_neg and ok_pos)
          else "  -> DO NOT PROCEED, fix the harness first")
    return 0 if (ok_neg and ok_pos) else 1


if __name__ == "__main__":
    raise SystemExit(main())
