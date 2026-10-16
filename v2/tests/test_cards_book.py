"""A second yellow is one booking and one dismissal, not two bookings.

SofaScore's statistics endpoint counts a second yellow twice - in the yellow
total as a booking, and in the red total as the dismissal it caused. Our card
figure was yellows + 2*reds, so a player sent off for two bookings was counted
four times where a bookmaker counts three.

SC Freiburg v FC Schalke, 2026-10-11, is the case that exposed it. Four
yellows, two of them to Yuito Suzuki, the second sending him off:

    49'  yellow     Suzuki
    57'  yellow     Ginter
    64'  yellowRed  Suzuki      <- second booking, becomes the red
    87'  yellow     Hwang

Books settle Suzuki as one yellow plus one red (3 cards), so the match is
1 + 1 + 3 = 5 cards. The old formula gave 6. The Over 4.5 won either way,
which is exactly why this could have gone unnoticed.
"""
from __future__ import annotations

import pytest

from v2.model.features import cards_book


def test_the_freiburg_match():
    assert cards_book(yellows=4, reds=1, second_yellow_reds=1) == 5


def test_a_straight_red_is_not_discounted():
    """Nothing to subtract: the dismissal was not preceded by its own booking."""
    assert cards_book(yellows=4, reds=1, second_yellow_reds=0) == 6


@pytest.mark.parametrize("y,r,s,expected", [
    (0, 0, 0, 0),
    (3, 0, 0, 3),
    (5, 2, 2, 7),     # both dismissals were second yellows
    (5, 2, 1, 8),     # one second yellow, one straight red
    (5, 2, 0, 9),     # two straight reds
])
def test_combinations(y, r, s, expected):
    assert cards_book(y, r, s) == expected


def test_unknown_split_reproduces_the_old_figure():
    """None means "not established", and must not be mistaken for zero in the
    sense of "we checked and there were none". 1,343 historical matches from
    football-data have no SofaScore id, so their split cannot be recovered;
    they keep the pre-existing figure rather than acquiring a new error."""
    assert cards_book(4, 1, None) == cards_book(4, 1, 0) == 6


def test_a_second_yellow_never_outnumbers_the_reds():
    """Guards the subtraction: more second yellows than reds would push the
    total below the cards actually shown."""
    assert cards_book(4, 1, 1) >= 4
