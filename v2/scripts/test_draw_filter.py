"""Does v1's DRAW filter beat the market? Six years, real closing odds.

v1's draw single is the only component of v1 with a live record that survived
scrutiny: 6 of 8 at an average 3.62 against a market-implied ~26%, roughly a
1-in-185 run. It is also the only scorer v1 grid-searched rather than built
from invented multipliers.

Its gate, from src/recommend/send_weekly.py:

    pd >= 0.29  and  |pts5_diff| <= 1  and  home_dr10 > 0.20  and  away_dr10 > 0.20

where pd is the de-vigged draw probability, pts5_diff the difference in points
from each side's last five VENUE matches, and dr10 the draw rate over each
side's last ten venue matches.

An earlier scan tested "back EVERY draw" over 7,975 matches: -0.69%. That is
not the same question. This tests the filter.

Runs entirely from the local cache - no database needed.

    python -m v2.scripts.test_draw_filter
"""
from __future__ import annotations

import json
import random
from collections import defaultdict, deque

import numpy as np

from v2.lib.config import V2
from v2.model.lineshop import devig_three_way

HOLDOUT_FROM = "2024-07-01"


def build():
    rows = json.loads((V2 / ".cache" / "football_data.json").read_text())
    rows = [r for r in rows if r.get("home_goals") is not None]
    rows.sort(key=lambda r: (r["date"], r["league"]))

    # venue-split stores, updated only AFTER a row is emitted
    res10 = defaultdict(lambda: deque(maxlen=10))   # (team,venue) -> 'W'/'D'/'L'
    pts5 = defaultdict(lambda: deque(maxlen=5))     # (team,venue) -> points
    out = []
    for r in rows:
        ht, at, lg = r["home_team"], r["away_team"], r["league"]
        kh, ka = (lg, ht, "home"), (lg, at, "away")
        h, d, a = (r.get("pinnacle_close_h"), r.get("pinnacle_close_d"),
                   r.get("pinnacle_close_a"))
        if h and d and a and len(res10[kh]) >= 10 and len(res10[ka]) >= 10:
            _, pd_, _ = devig_three_way(float(h), float(d), float(a))
            hdr = sum(1 for x in res10[kh] if x == "D") / len(res10[kh])
            adr = sum(1 for x in res10[ka] if x == "D") / len(res10[ka])
            gap = abs(sum(pts5[kh]) - sum(pts5[ka]))
            out.append(dict(date=r["date"], league=lg, home=ht, away=at,
                            pd=pd_, hdr=hdr, adr=adr, gap=gap,
                            price=float(d),
                            drew=(r["home_goals"] == r["away_goals"])))
        hg, ag = r["home_goals"], r["away_goals"]
        rh = "W" if hg > ag else ("D" if hg == ag else "L")
        ra = "W" if ag > hg else ("D" if hg == ag else "L")
        res10[kh].append(rh); res10[ka].append(ra)
        pts5[kh].append(3 if rh == "W" else (1 if rh == "D" else 0))
        pts5[ka].append(3 if ra == "W" else (1 if ra == "D" else 0))
    return out


def report(name, sel, rng):
    if len(sel) < 30:
        print(f"  {name:44s} n={len(sel):4d}   (too few)"); return
    pnl = [(s["price"] - 1) if s["drew"] else -1 for s in sel]
    roi = float(np.mean(pnl))
    sims = sorted(np.mean([rng.choice(pnl) for _ in pnl]) for _ in range(3000))
    hit = np.mean([s["drew"] for s in sel])
    star = " *" if sims[75] > 0 else ""
    print(f"  {name:44s} n={len(sel):4d}  hit={hit:5.1%}  avg={np.mean([s['price'] for s in sel]):4.2f}  "
          f"ROI={roi:+6.1%}  [{sims[75]:+.1%},{sims[2925]:+.1%}]{star}")


def main() -> int:
    data = build()
    rng = random.Random(7)
    print(f"{len(data)} matches with closing 1X2 odds and 10 venue games of history\n")

    V1 = lambda s: s["pd"] >= 0.29 and s["gap"] <= 1 and s["hdr"] > 0.20 and s["adr"] > 0.20
    for label, pool in (("FULL SAMPLE", data),
                        ("EXPLORATORY (pre 2024-07)", [s for s in data if s["date"] < HOLDOUT_FROM]),
                        ("HELD OUT (2024-07 on)", [s for s in data if s["date"] >= HOLDOUT_FROM])):
        print(f"=== {label} ===")
        report("back every draw (baseline)", pool, rng)
        report("v1 filter: pd>=.29, gap<=1, both dr10>.20", [s for s in pool if V1(s)], rng)
        print()

    print("=== which gate is doing the work? (full sample) ===")
    report("pd >= 0.29 only", [s for s in data if s["pd"] >= 0.29], rng)
    report("gap <= 1 only", [s for s in data if s["gap"] <= 1], rng)
    report("both dr10 > 0.20 only", [s for s in data if s["hdr"] > 0.20 and s["adr"] > 0.20], rng)
    report("pd>=.29 AND gap<=1", [s for s in data if s["pd"] >= 0.29 and s["gap"] <= 1], rng)
    print("\n  * = 95% bootstrap CI excludes zero")
    print("  break-even at the ~3.6 average price is a 27.8% hit rate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
