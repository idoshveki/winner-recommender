"""Two different quantities wear the word "cards", and settling one with the
other is a silent, expensive error.

  "YC ..."    v1's market. 1win prices YELLOW cards and carries red cards as a
              separate market, so these settle on yellows alone.
  "Cards ..." v2's market. It prices Pinnacle's alternate_totals_cards and its
              model target is target_cards_book = yellows + 2*reds, so a red
              counts as TWO.

The case that forced this apart: v2 took Over 4.5 on SC Freiburg v FC Schalke
at 3.00. The match finished with 4 yellows and 1 red - 4 by the yellows rule
(a loss), 6 by the cards rule (a win). The settler graded every card leg on
yellows, so it would have recorded a loss on a bet its own market won, and the
two earlier v2 picks only agreed because neither match had a red card.
"""
from __future__ import annotations

import pytest

from v2.ingest.settle import grade


# (market, pick, hg, ag, yellows, cards_book, expected)
CASES = [
    # the real divergence
    ("Cards Over 4.5", "Over 4.5", 2, 1, 4, 6, True),    # cards rule wins
    ("YC Over 4.5",    "Over 4.5", 2, 1, 4, 6, False),   # yellows rule loses

    # the two already-settled v2 picks, where both rules agreed
    ("Cards Under 3.5", "Under 3.5", 1, 0, 2, 2, True),
    ("Cards Over 3.5",  "Over 3.5",  2, 2, 3, 3, False),

    # v1's yellows market must ignore reds entirely
    ("YC Over 3.5", "Over 3.5", 0, 0, 9, 13, True),
    ("YC Over 3.5", "Over 3.5", 0, 0, 3, 7, False),

    # straight results
    ("H/A", "H", 3, 0, None, None, True),
    ("H/A", "H", 0, 3, None, None, False),
    ("H/A", "A", 0, 3, None, None, True),
    ("H/A", "D", 1, 1, None, None, True),
    ("H/A", "D", 2, 1, None, None, False),
]


@pytest.mark.parametrize("market,pick,hg,ag,yellows,cards,expected", CASES)
def test_grade(market, pick, hg, ag, yellows, cards, expected):
    assert grade(market, pick, hg, ag, yellows, cards) is expected


def test_cards_leg_is_ungradeable_without_a_cards_total():
    """None, not a guess, and specifically not a loss. A pending leg scored as
    a loss is the one arithmetic error the whole record exists to prevent."""
    assert grade("Cards Over 4.5", "Over 4.5", 1, 0, 4, None) is None


def test_yellow_leg_is_ungradeable_without_yellows():
    assert grade("YC Over 3.5", "Over 3.5", 1, 0, None, 6) is None


def test_a_cards_market_never_silently_uses_yellows():
    """The regression itself: with a red on the pitch the two rules disagree,
    and a "Cards" market must follow the cards total."""
    yellows, cards = 4, 6
    assert grade("Cards Over 4.5", "Over 4.5", 2, 1, yellows, cards) is True
    assert grade("Cards Over 5.5", "Over 5.5", 2, 1, yellows, cards) is True
    assert grade("Cards Over 6.5", "Over 6.5", 2, 1, yellows, cards) is False


def test_malformed_pick_does_not_settle():
    assert grade("Cards Over", "Over", 1, 0, 4, 6) is None
