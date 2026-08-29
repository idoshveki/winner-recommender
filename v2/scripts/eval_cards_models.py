"""Compare card-total models on a strict temporal split.

Contenders:
  base       league base rate (the number to beat before anything else)
  poisson    league-mean Poisson (knows only the league)
  glm        existing counts.py - Poisson GLM on the match total
  teamconv   MODEL A - per-team GLMs convolved, dispersion from residuals
  gbm        MODEL B - per-line gradient boosting, no count distribution

    python -m v2.scripts.eval_cards_models
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy.stats import poisson as pois

from v2.lib.jobs import connect
from v2.model import cards_models, counts, features

TRAIN_END = "2024-06-30"
LINES = (2.5, 3.5, 4.5, 5.5)


def load(conn):
    return [
        dict(match_id=r[0], league=r[1], kickoff_utc=r[2], home_team_id=r[3],
             away_team_id=r[4], home_yellow=r[5], away_yellow=r[6],
             home_corners=r[7], away_corners=r[8], home_shots=r[9], away_shots=r[10],
             home_fouls=r[11], away_fouls=r[12], home_goals=r[13], away_goals=r[14],
             odds_h=r[15], odds_d=r[16], odds_a=r[17],
             home_red=r[18], away_red=r[19])
        for r in conn.execute("""
            select m.id, m.league, m.kickoff_utc, m.home_team_id, m.away_team_id,
                   s.home_yellow, s.away_yellow, s.home_corners, s.away_corners,
                   s.home_shots, s.away_shots, s.home_fouls, s.away_fouls,
                   s.home_goals, s.away_goals,
                   coalesce(o.pinnacle_close_h, o.avg_h),
                   coalesce(o.pinnacle_close_d, o.avg_d),
                   coalesce(o.pinnacle_close_a, o.avg_a),
                   s.home_red, s.away_red
            from matches m
            join match_stats s on s.match_id=m.id
            left join historical_odds o on o.match_id=m.id
            where m.status='finished' and s.home_yellow is not null
            order by m.kickoff_utc, m.id""")
    ]


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def logloss(ps, ys):
    e = 1e-9
    return -sum(y * math.log(max(p, e)) + (1 - y) * math.log(max(1 - p, e))
                for p, y in zip(ps, ys)) / len(ys)


def main() -> int:
    with connect() as conn:
        rows = load(conn)
    feats = features.build(rows)
    train = [f for f in feats if str(f.kickoff)[:10] < TRAIN_END]
    test = [f for f in feats if str(f.kickoff)[:10] >= TRAIN_END
            and f.usable() and f.target_cards_book is not None]
    print(f"train {len(train)} matches (< {TRAIN_END})   test {len(test)}\n")

    glm = counts.train(train, "target_cards_book", features.CARD_FEATURES, "cards")
    conv = cards_models.train_team_conv(train)
    gbm = cards_models.train_line_gbm(train, LINES)
    print(f"  glm      leagues {sorted(glm.by_league)}")
    print(f"  teamconv leagues {sorted(conv.by_league)}  "
          f"dispersion {[round(m.dispersion,1) if m.dispersion else None for m in conv.by_league.values()]}")
    print(f"  gbm      fitted {len(gbm.by_key)} league-line models\n")

    # league base rates and league mean, from TRAIN only
    base = {}
    lgmean = {}
    for lg in sorted({f.league for f in train}):
        sub = [f.target_cards_book for f in train if f.league == lg and f.target_cards_book is not None]
        lgmean[lg] = float(np.mean(sub))
        for L in LINES:
            base[(lg, L)] = sum(1 for x in sub if x > L) / len(sub)

    print(f"{'line':>5s} {'n':>5s} {'actual':>7s} | "
          + " ".join(f"{n:>17s}" for n in ("base", "poisson", "glm", "teamconv", "gbm")))
    print(f"{'':>5s} {'':>5s} {'':>7s} | " + " ".join(f"{'brier   logloss':>17s}" for _ in range(5)))
    summary = defaultdict(list)
    for L in LINES:
        preds = defaultdict(list); ys = []
        for f in test:
            p_glm = glm.p_over(f, L)
            p_cnv = conv.p_over(f, L)
            p_gbm = gbm.p_over(f, L)
            if p_glm is None or p_cnv is None or p_gbm is None:
                continue
            ys.append(1 if f.target_cards_book > L else 0)
            preds["base"].append(base[(f.league, L)])
            mu = lgmean[f.league]
            preds["poisson"].append(1 - pois(mu).cdf(math.floor(L)))
            preds["glm"].append(p_glm)
            preds["teamconv"].append(p_cnv)
            preds["gbm"].append(p_gbm)
        if not ys:
            continue
        cells = []
        for k in ("base", "poisson", "glm", "teamconv", "gbm"):
            b, l = brier(preds[k], ys), logloss(preds[k], ys)
            summary[k].append(b)
            cells.append(f"{b:.4f}  {l:.4f}")
        print(f"{L:5.1f} {len(ys):5d} {sum(ys)/len(ys):6.1%} | " + " ".join(f"{c:>17s}" for c in cells))

    print(f"\n{'mean brier across lines':30s}", end="")
    for k in ("base", "poisson", "glm", "teamconv", "gbm"):
        print(f"  {k}={np.mean(summary[k]):.4f}", end="")
    print()
    best = min(summary, key=lambda k: np.mean(summary[k]))
    print(f"  best: {best}")
    imp = (np.mean(summary['base']) - np.mean(summary[best])) / np.mean(summary['base'])
    print(f"  improvement of {best} over league base rate: {imp:+.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
