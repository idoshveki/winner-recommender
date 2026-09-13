from v2.model.markets import LOSS, PUSH, WIN, pnl, settle_handicap, settle_total
import pytest


class TestTotals:
    def test_half_line_never_pushes(self):
        assert settle_total(4, 3.5, "over") == WIN
        assert settle_total(3, 3.5, "over") == LOSS
        assert settle_total(3, 3.5, "under") == WIN
        assert settle_total(4, 3.5, "under") == LOSS

    def test_integer_line_pushes_on_exact(self):
        assert settle_total(3, 3.0, "over") is PUSH
        assert settle_total(3, 3.0, "under") is PUSH
        assert settle_total(4, 3.0, "over") == WIN
        assert settle_total(2, 3.0, "over") == LOSS

    def test_a_push_is_not_a_win(self):
        """v1 had no push handling, which overstated every integer line."""
        assert settle_total(3, 3.0, "over") is not WIN
        assert pnl(settle_total(3, 3.0, "over"), 2.5, stake=50) == 0.0

    def test_rejects_a_bad_side(self):
        with pytest.raises(ValueError):
            settle_total(3, 3.5, "yes")


class TestHandicap:
    def test_half_point_is_asymmetric_on_equal_counts(self):
        """The whole point of +0.5 vs -0.5: an equal count covers one and not
        the other. Getting this backwards flips every drawn-cards match."""
        assert settle_handicap(own=2, opponent=2, point=+0.5) == WIN
        assert settle_handicap(own=2, opponent=2, point=-0.5) == LOSS

    def test_minus_half_needs_strictly_more(self):
        assert settle_handicap(own=3, opponent=2, point=-0.5) == WIN
        assert settle_handicap(own=2, opponent=3, point=-0.5) == LOSS

    def test_plus_half_survives_losing_by_nothing_but_not_by_one(self):
        assert settle_handicap(own=2, opponent=2, point=+0.5) == WIN
        assert settle_handicap(own=2, opponent=3, point=+0.5) == LOSS

    def test_whole_number_handicap_can_push(self):
        assert settle_handicap(own=3, opponent=2, point=-1.0) is PUSH
        assert settle_handicap(own=4, opponent=2, point=-1.0) == WIN
        assert settle_handicap(own=2, opponent=2, point=0.0) is PUSH

    def test_the_real_liverpool_brighton_quote(self):
        """Brighton -0.5 @2.03 / Liverpool +0.5 @1.73, as captured from
        Pinnacle. Exactly one side must win, whatever the card counts."""
        for lfc, bha in ((0, 0), (1, 3), (3, 1), (2, 2), (5, 4)):
            brighton = settle_handicap(own=bha, opponent=lfc, point=-0.5)
            liverpool = settle_handicap(own=lfc, opponent=bha, point=+0.5)
            assert {brighton, liverpool} == {WIN, LOSS}, (lfc, bha)


class TestPnl:
    def test_win_pays_price_minus_stake(self):
        assert pnl(WIN, 2.5, stake=50) == pytest.approx(75.0)

    def test_loss_costs_the_stake(self):
        assert pnl(LOSS, 2.5, stake=50) == -50.0

    def test_push_returns_the_stake(self):
        assert pnl(PUSH, 2.5, stake=50) == 0.0
