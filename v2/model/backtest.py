"""Honest backtest: train on the past, bet at real prices, settle on real results.

Rules enforced here, each one a v1 failure:
  * strict temporal split - the model never sees a match at or after the cutoff
  * features come from the same module inference uses, so "backtested" describes
    the code that runs
  * bets are priced at REAL Pinnacle prices captured 3h before kickoff, never
    at an assumed constant
  * integer lines push; a push returns the stake rather than counting as a win
  * every threshold is declared up front, not chosen after seeing the result

    python -m v2.model.backtest [--cutoff 2026-02-01] [--min-edge 0.05]
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from v2.lib.jobs import connect
from v2.model import counts, features
from v2.model.devig import DEVIG_METHODS, fit_ladder, p_over_from_dist

CARD_LINES = (2.5, 3.5, 4.5, 5.5)
CORNER_LINES = (8.5, 9.5, 10.5, 11.5)


def load_rows(conn) -> List[dict]:
    return [
        dict(match_id=r[0], league=r[1], kickoff_utc=r[2], home_team_id=r[3],
             away_team_id=r[4], home_yellow=r[5], away_yellow=r[6],
             home_corners=r[7], away_corners=r[8], home_shots=r[9],
             away_shots=r[10], odds_h=r[11], odds_d=r[12], odds_a=r[13])
        for r in conn.execute("""
            select m.id, m.league, m.kickoff_utc, m.home_team_id, m.away_team_id,
                   s.home_yellow, s.away_yellow, s.home_corners, s.away_corners,
                   s.home_shots, s.away_shots,
                   coalesce(o.pinnacle_close_h, o.avg_h),
                   coalesce(o.pinnacle_close_d, o.avg_d),
                   coalesce(o.pinnacle_close_a, o.avg_a)
            from matches m
            join match_stats s on s.match_id = m.id
            left join historical_odds o on o.match_id = m.id
            where m.status = 'finished'
            order by m.kickoff_utc, m.id""")
    ]


def load_ladders(conn) -> Dict[Tuple[int, str], Dict[float, Dict[str, float]]]:
    """{(match_id, market): {line: {'over': price, 'under': price}}} from Pinnacle."""
    out: Dict[Tuple[int, str], Dict[float, Dict[str, float]]] = defaultdict(dict)
    for match_id, market, line, side, price in conn.execute("""
            select match_id, market, line, side, price
            from odds_snapshots
            where bookmaker = 'pinnacle' and line is not null"""):
        out[(match_id, market)].setdefault(float(line), {})[side.lower()] = float(price)
    return out


def settle(total: int, line: float, side: str, price: float) -> Optional[float]:
    """Profit per 1 unit staked. None when the bet is void."""
    if abs(line - round(line)) < 1e-9 and total == int(round(line)):
        return 0.0                       # push: stake returned
    won = total > line if side == "over" else total < line
    return (price - 1.0) if won else -1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2026-02-01")
    ap.add_argument("--min-edge", type=float, default=0.05)
    ap.add_argument("--devig", default="shin")
    ap.add_argument("--one-per-market", action="store_true",
                    help="keep only the single best edge per match and market")
    args = ap.parse_args()

    with connect() as conn:
        rows = load_rows(conn)
        ladders = load_ladders(conn)
    print(f"loaded {len(rows)} matches, {len(ladders)} priced match-markets")

    feats = features.build(rows)
    by_id = {f.match_id: f for f in feats}
    cutoff = args.cutoff

    train_rows = [f for f in feats if str(f.kickoff)[:10] < cutoff]
    test_ids = {mid for (mid, _) in ladders}
    test_rows = [f for f in feats if f.match_id in test_ids and str(f.kickoff)[:10] >= cutoff]
    print(f"train: {len(train_rows)} matches before {cutoff}")
    print(f"test:  {len(test_rows)} matches with real Pinnacle ladders\n")
    if not test_rows:
        print("no test matches - fetch historical prices first")
        return 1

    models = {
        "totals_cards": counts.train(train_rows, "target_cards",
                                     features.CARD_FEATURES, "totals_cards"),
        "totals_corners": counts.train(train_rows, "target_corners",
                                       features.CORNER_FEATURES, "totals_corners"),
    }
    for market, m in models.items():
        detail = ", ".join(
            f"{lg}:n={lm.n_train},disp={round(lm.dispersion,1) if lm.dispersion else 'poisson'}"
            for lg, lm in sorted(m.by_league.items()))
        print(f"  {market}: {detail}")
    print()

    devig = DEVIG_METHODS[args.devig]
    actuals = {r["match_id"]: r for r in rows}
    bets = []

    for market, lines in (("totals_cards", CARD_LINES), ("totals_corners", CORNER_LINES)):
        model = models[market]
        target = "target_cards" if market == "totals_cards" else "target_corners"
        for mid in sorted(test_ids):
            key = (mid, market)
            if key not in ladders:
                continue
            row = by_id.get(mid)
            if row is None or not row.usable():
                continue
            total = getattr(row, target)
            if total is None:
                continue
            ladder = ladders[key]
            two_sided = {l: v for l, v in ladder.items() if "over" in v and "under" in v}
            if len(two_sided) < 2:
                continue
            # fit the book's implied distribution across its whole ladder
            pts = [(l, devig(v["over"], v["under"])) for l, v in sorted(two_sided.items())]
            try:
                book = fit_ladder(pts)
            except Exception:
                continue

            candidates = []
            for line, prices in sorted(ladder.items()):
                p_model = model.p_over(row, line)
                if p_model is None:
                    continue
                for side in ("over", "under"):
                    if side not in prices:
                        continue
                    price = prices[side]
                    p = p_model if side == "over" else 1.0 - p_model
                    p_book = book.p_over(line)
                    p_book = p_book if side == "over" else 1.0 - p_book
                    edge = p * price - 1.0
                    if edge < args.min_edge:
                        continue
                    candidates.append({
                        "match_id": mid, "league": row.league, "kickoff": row.kickoff,
                        "market": market, "line": line, "side": side, "price": price,
                        "p_model": p, "p_book": p_book, "edge": edge,
                        "total": total,
                        "pnl": settle(total, line, side, price),
                    })
            if args.one_per_market and candidates:
                candidates = [max(candidates, key=lambda c: c["edge"])]
            bets.extend(candidates)

    calibration(models, test_rows, ladders, devig)

    if not bets:
        print(f"NO BETS cleared the {args.min_edge:.0%} edge threshold.")
        print("That is a result, not a bug - the model never disagreed with")
        print("Pinnacle by enough to be worth a stake.")
        return 0

    report(bets, args)
    return 0


def calibration(models, test_rows, ladders, devig) -> None:
    """Model vs book vs reality across EVERY priced test match.

    The bet-level numbers are a selected subsample - we bet precisely where we
    most disagree with the market, which is also where our own error is
    largest. This is the unselected view, and it is the one that says whether
    the model is any good.
    """
    print("=== calibration on ALL priced test matches (no bet selection) ===")
    by_id = {r.match_id: r for r in test_rows}
    for market, lines, target in (
        ("totals_cards", CARD_LINES, "target_cards"),
        ("totals_corners", CORNER_LINES, "target_corners"),
    ):
        model = models[market]
        print(f"  {market}")
        print(f"    {'line':>5s} {'n':>4s} {'model':>7s} {'book':>7s} {'actual':>7s} "
              f"{'model err':>10s} {'book err':>9s}")
        for line in lines:
            m_ps, b_ps, ys = [], [], []
            for (mid, mk), ladder in ladders.items():
                if mk != market or mid not in by_id:
                    continue
                row = by_id[mid]
                total = getattr(row, target)
                if total is None or not row.usable():
                    continue
                p_model = model.p_over(row, line)
                if p_model is None:
                    continue
                two = {l: v for l, v in ladder.items() if "over" in v and "under" in v}
                if len(two) < 2:
                    continue
                try:
                    book = fit_ladder([(l, devig(v["over"], v["under"]))
                                       for l, v in sorted(two.items())])
                except Exception:
                    continue
                m_ps.append(p_model)
                b_ps.append(book.p_over(line))
                ys.append(1.0 if total > line else 0.0)
            if len(ys) < 5:
                continue
            n = len(ys)
            mm, bb, aa = sum(m_ps)/n, sum(b_ps)/n, sum(ys)/n
            m_brier = sum((p - y) ** 2 for p, y in zip(m_ps, ys)) / n
            b_brier = sum((p - y) ** 2 for p, y in zip(b_ps, ys)) / n
            flag = "  <-- model beats book" if m_brier < b_brier else ""
            print(f"    {line:5.1f} {n:4d} {mm:7.1%} {bb:7.1%} {aa:7.1%} "
                  f"{m_brier:10.4f} {b_brier:9.4f}{flag}")
    print()


def report(bets: List[dict], args) -> None:
    def summarise(label: str, subset: List[dict]) -> None:
        if not subset:
            return
        settled = [b for b in subset if b["pnl"] is not None]
        pushes = [b for b in settled if b["pnl"] == 0.0]
        wins = [b for b in settled if b["pnl"] > 0]
        pnl = sum(b["pnl"] for b in settled)
        staked = len(settled)
        avg_price = sum(b["price"] for b in settled) / staked
        avg_edge = sum(b["edge"] for b in settled) / staked
        decided = [b for b in settled if b["pnl"] != 0.0]
        wr = len(wins) / len(decided) if decided else 0.0
        print(f"  {label:22s} n={staked:4d}  win={wr:6.1%}  avg_odds={avg_price:5.2f}  "
              f"avg_edge={avg_edge:+6.1%}  P&L={pnl:+8.2f}u  ROI={pnl/staked:+7.2%}"
              f"{'  pushes=' + str(len(pushes)) if pushes else ''}")

    print(f"=== BACKTEST: bets at real Pinnacle prices, {args.min_edge:.0%} min edge ===")
    summarise("ALL", bets)
    print()
    for market in sorted({b["market"] for b in bets}):
        summarise(market, [b for b in bets if b["market"] == market])
    print()
    for side in ("over", "under"):
        summarise(side, [b for b in bets if b["side"] == side])
    print()
    for league in sorted({b["league"] for b in bets}):
        summarise(league, [b for b in bets if b["league"] == league])

    print("\n  model vs market calibration on the bets placed:")
    settled = [b for b in bets if b["pnl"] is not None and b["pnl"] != 0.0]
    if settled:
        won = sum(1 for b in settled if b["pnl"] > 0) / len(settled)
        print(f"    model said {sum(b['p_model'] for b in settled)/len(settled):.1%}, "
              f"book said {sum(b['p_book'] for b in settled)/len(settled):.1%}, "
              f"actual {won:.1%}")

    print("\n  sample of the largest edges:")
    for b in sorted(bets, key=lambda x: -x["edge"])[:8]:
        res = "PUSH" if b["pnl"] == 0 else ("WON " if b["pnl"] > 0 else "LOST")
        print(f"    {str(b['kickoff'])[:10]} {b['league']:11s} "
              f"{b['market'].replace('totals_',''):8s} {b['side']:5s} {b['line']:5.1f} "
              f"@{b['price']:5.2f}  model {b['p_model']:5.1%} book {b['p_book']:5.1%} "
              f"edge {b['edge']:+5.1%}  actual={b['total']:2d} {res} {b['pnl']:+6.2f}u")


if __name__ == "__main__":
    raise SystemExit(main())
