"""The headline total and the week badges must tell the same story.

They were computed by two different routes - the headline from the slips,
draws and singles lists, the badges from each week's own sum - and the badge
route added the draw twice. Every one of the eight weeks carrying a draw was
overstated; 2026-09-11 read +5.60u against a true +2.30u. The headline was
right the whole time, which is why nobody caught it: the two numbers were
never compared.

Reconciling them is the test. If a future change makes one route disagree with
the other, one of them is lying about money.
"""
from __future__ import annotations

from v2.scripts.build_dashboard import analyse, sample_rows


def _analysis():
    rows, meta = sample_rows()
    return analyse(rows, meta.get("weeks_view"))


def test_week_badges_sum_to_the_headline():
    a = _analysis()
    badges = sum(w["pnl"] for w in a["weeks"] if w["pnl"] is not None)
    assert abs(badges - a["overall"]["pnl"]) < 1e-9, (
        f"week badges total {badges:+.4f} but the headline says "
        f"{a['overall']['pnl']:+.4f}")


def test_a_draw_is_counted_once_per_week():
    a = _analysis()
    for w in a["weeks"]:
        if w["draw_pnl"] is None:
            continue
        parts = (w["slip"] or {}).get("pnl") or 0.0
        parts += w.get("singles_pnl") or 0.0
        assert abs(w["pnl"] - (parts + w["draw_pnl"])) < 1e-9, (
            f"{w['date']} {w['system']}: badge {w['pnl']:+.4f} does not equal "
            f"slip+singles+draw")


def test_bet_count_matches_the_parts():
    a = _analysis()
    assert a["overall"]["staked"] == (
        a["slips"]["staked"] + a["draws"]["staked"] + a["singles"]["staked"])
