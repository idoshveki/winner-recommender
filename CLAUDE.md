# Winner Recommender — status and findings

Two systems live in this repo.

- **v1** — everything outside `v2/`. The original recommender. **Still running**:
  it emails picks every Friday (`[v1] Winner Picks …`) via four GitHub Actions.
- **v2** — everything under `v2/`. A rebuild that became an investigation.
  Postgres on Supabase, 8,700+ matches, models, a validated test harness.

**Read `v2/docs/FINDINGS.md` before proposing any strategy.** Every measurement
is recorded there with the number that killed it. This file summarises.

---

## Headline: no demonstrated edge

Seven theses tested against real closing prices. All dead.

| thesis | result |
|---|---|
| v1's track record | **fabricated** — 27 of 29 "settled" weeks were backtest rows loaded into the live table |
| model beats market on cards | loses (−5% vs Pinnacle's own price) |
| model beats market on corners | matches Brier, never beats |
| cards handicap from fouls | loses |
| line shopping, soft book vs sharp | −3.9% to −46% held out, monotonically worse with more edge demanded |
| 86 systematic category rules | best is the **40th percentile of pure noise** |
| v1's draw filter | real skill (25.3% → 31.6%) but **exactly break-even** |

Live record since March 2026 (`v1_live_record`, auto-settled): slips **5 won /
6 lost, −6.2%**; draw singles **6 of 8, +170%**; overall +12.9u on 19 bets. The
draws are a 1-in-185 run on a strategy measured as break-even — a hot streak,
not evidence.

**The market prices these sports efficiently and our data holds nothing it
lacks.** Anything claiming otherwise needs to clear the gates below.

---

## Traps that have already cost real money or nearly did

1. **Prices must be observed, not assumed.** v1 hardcoded **1.50** for cards
   Over 3.5 across 32 picks. 1win actually pays 1.36–2.00 and it varies by
   match. Every v1 EV number rests on a price that does not exist.
2. **Books settle a red card as TWO cards.** We modelled yellows only, which
   undercounts every match, made every Under look cheap, and manufactured a
   "+16.8% edge" that vanished on correction.
3. **1win prices YELLOWS; Pinnacle prices CARDS.** Different quantities. Reading
   the gap as a pricing bias produced a losing recommendation with a
   predictable sign.
4. **Opening odds are not closing odds.** `PSH`/`Avg>2.5` are the OPEN;
   `PSCH`/`AvgC>2.5` are the CLOSE. Beating an opening line is easy and
   meaningless. This error was made twice.
5. **Statistical significance is not money.** A perfectly efficient market threw
   off `c=+0.507, p=0.044`. Only the money test caught it.
6. **Silent success is the house bug.** v1's ingest printed "0 rows updated" and
   exited 0 for five months. Since then: a pipeline masking an exit code through
   `grep`, and an `ON CONFLICT` discarding every row while reporting success.
   **A job that writes nothing when it should write something must fail.**

---

## Gates any new strategy must clear

Non-negotiable, and each exists because something failed without it.

- **Out-of-sample money test with edge monotonicity.** ROI must not decline as
  the minimum edge rises. Noise gets worse when you demand more edge, because
  filtering for your largest disagreements concentrates your own errors.
- **Held-out period, read once.** Thresholds fit on train only.
- **Permutation null over the whole search.** With 79 rules and ~57 bets each,
  a pure-noise null produces a best-rule ROI of **+49% median**. Report where
  the observed best sits in that distribution.
- **Sanity ceiling.** A probability more than ~10 points from a sharp book is a
  bug, not an edge. Discard it and find the bug.
- **Pre-register** markets, sample, split and thresholds before fetching odds.
- **Report n and a bootstrap CI on everything.** Most samples here are 8–150.

---

## What runs

| workflow | when | does |
|---|---|---|
| `v1 — Send Weekly Picks` | Fri 08:00 IL | emails picks (model has no measured edge) |
| `v1 — Fetch Odds` | daily | works |
| `v1 — Fetch Match Results` | Tue/Fri | **broken since March** — reads gitignored CSVs, has never written a row |
| `v1 — Auto-fill Pick Results` | Tue/Fri | broken, depends on the above |
| `v2 — daily` | 07:15 IL | ingest results → track/settle v1 picks → v2 picks → freshness → dashboard |
| `v2 — publish dashboard` | after v2-daily | GitHub Pages |

v2 emails nothing. Its picker records what it *would* bet plus a `qualifies`
flag, so v1 (picks every week) and v2 (picks only on a passing gate) can be
compared over a season.

Supabase free tier **pauses after ~7 idle days** — it has done so twice, both
times silently. The daily job exists partly to keep it warm and fails loudly if
it cannot connect.

---

## Key paths

```
v2/docs/FINDINGS.md          every measurement, including the negative ones
v2/docs/PREREGISTRATION*.md  commitments made before results were seen
v2/model/experiment.py       edge_monotonicity, BH-FDR, the gates
v2/model/markets.py          settlement (pushes, handicap +0.5/-0.5 asymmetry)
v2/model/onewin.py           1win pricing: yellows = Pinnacle cards − 2×reds
v2/scripts/build_feature_mart.py   team_match_form + match_features
v2/ingest/v1_picks.py        records and settles v1's picks automatically
v2/recommend/picks.py        v2's picker
```

Run anything with `PYTHONPATH=. v2/.venv/bin/python -m <module>`.

---

## What NOT to do

- **Do not bet on v1's output.** It has no measured edge and its stated odds for
  cards legs are fictional.
- **Do not add legs to a slip.** Same picks bet as singles returned +88%; bundled
  into accumulators, −5%. Every extra leg multiplies the margin against you.
- **Do not report a hit rate without its price.** 90% rules exist in abundance
  and pay 1.13. Hit rate and price are the same number.
- **Do not slice by league/team hunting for a winner.** With four leagues, a
  pure-noise null gives a best-league ROI of +17% half the time.
- **Do not trust a derived price.** The 1win price model has ~21% error on
  low-profile fixtures. Capture real quotes before claiming edge.
- **Do not delete a negative result.** They are the most valuable thing here.
