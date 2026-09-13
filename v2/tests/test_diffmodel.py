"""The handicap cover probabilities must mirror exactly, or every drawn-cards
match is graded backwards."""
from __future__ import annotations

import numpy as np
import pytest

from v2.model.diffmodel import LeagueDiffModel


def model(mu_h: float, mu_a: float) -> LeagueDiffModel:
    """A degenerate one-feature model whose predicted means are fixed."""
    return LeagueDiffModel(
        league="X", feature_names=["k"],
        params_home=np.array([np.log(mu_h), 0.0]),
        params_away=np.array([np.log(mu_a), 0.0]),
        means=np.array([0.0]), n_train=999,
    )


class Row:
    league = "X"
    features = {"k": 0.0}
    def usable(self): return True


def test_mirrored_half_point_probabilities_sum_to_one():
    """home at -0.5 wins exactly when away at +0.5 loses."""
    m = model(2.2, 1.9)
    r = Row()
    assert m.p_cover(r, -0.5, "home") + m.p_cover(r, +0.5, "away") == pytest.approx(1.0, abs=1e-9)
    assert m.p_cover(r, +0.5, "home") + m.p_cover(r, -0.5, "away") == pytest.approx(1.0, abs=1e-9)


def test_equal_means_favour_the_plus_half_side():
    """With identical means the sides are symmetric, so the team receiving
    +0.5 must be better than even and the -0.5 side worse."""
    m = model(2.0, 2.0)
    r = Row()
    assert m.p_cover(r, +0.5, "home") > 0.5
    assert m.p_cover(r, -0.5, "home") < 0.5


def test_the_dirtier_team_is_more_likely_to_cover_minus_half():
    m_dirty = model(3.2, 1.5)
    m_clean = model(1.5, 3.2)
    r = Row()
    assert m_dirty.p_cover(r, -0.5, "home") > 0.5
    assert m_clean.p_cover(r, -0.5, "home") < 0.5


def test_real_pinnacle_quote_shape():
    """Brighton (away) -0.5 @2.03 / Liverpool (home) +0.5 @1.73. The away side
    priced longer, so the model must make the away -0.5 the less likely."""
    m = model(2.1, 1.9)          # home slightly dirtier
    r = Row()
    brighton = m.p_cover(r, -0.5, "away")
    liverpool = m.p_cover(r, +0.5, "home")
    assert brighton < liverpool
    assert brighton + liverpool == pytest.approx(1.0, abs=1e-9)


def test_the_card_LEVEL_moves_a_symmetric_handicap_via_ties():
    """Not obvious, and worth encoding: even with equal means, the total card
    level changes a -0.5 handicap. In a low-card match an exact tie is far more
    likely, and a tie loses -0.5. So the higher the expected card count, the
    better the -0.5 side.

    This is why the handicap cannot be modelled from the difference alone - the
    tie mass matters, which is exactly what the Skellam gives us."""
    r = Row()
    low = model(1.2, 1.2).p_cover(r, -0.5, "home")
    high = model(4.0, 4.0).p_cover(r, -0.5, "home")
    assert low < high, "more cards should make 'strictly more' easier"
    assert low < 0.5 and high < 0.5, "still worse than even at -0.5"
    assert high - low > 0.03, "the tie effect should be material, not trivial"
