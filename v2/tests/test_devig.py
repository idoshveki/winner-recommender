"""De-vigging and ladder fitting."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from scipy.stats import nbinom, poisson

from v2.model.devig import (
    DEVIG_METHODS, LadderFit, devig_proportional, devig_shin, fit_ladder,
    ladder_from_outcomes, overround, p_over_from_dist,
)

FIXTURE = Path(__file__).parent / "fixtures" / "odds_api_cards_corners.json"


def test_overround_of_a_fair_market_is_zero():
    assert overround(2.0, 2.0) == pytest.approx(0.0)


def test_overround_matches_hand_calculation():
    # real Pinnacle cards line: Over 3.5 @ 2.46 / Under 3.5 @ 1.54
    assert overround(2.46, 1.54) == pytest.approx(0.0559, abs=1e-4)


@pytest.mark.parametrize("method", sorted(DEVIG_METHODS))
def test_every_method_removes_all_margin(method):
    devig = DEVIG_METHODS[method]
    p_over = devig(2.46, 1.54)
    p_under = devig(1.54, 2.46)
    assert p_over + p_under == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("method", sorted(DEVIG_METHODS))
def test_devig_leaves_a_fair_market_untouched(method):
    assert DEVIG_METHODS[method](2.0, 2.0) == pytest.approx(0.5, abs=1e-6)


def test_shin_corrects_favourite_longshot_bias():
    """Books pad longshots more than favourites, because punters overbet them.
    Proportional de-vigging removes margin evenly and so leaves the longshot
    overstated. Shin attributes more of the margin to the longshot, which
    pushes the FAVOURITE's fair probability up relative to proportional."""
    prop = devig_proportional(1.22, 4.10)   # heavy favourite
    shin = devig_shin(1.22, 4.10)
    assert abs(prop - shin) > 0.002
    assert shin > prop
    # ... and correspondingly the longshot's fair probability drops
    assert devig_shin(4.10, 1.22) < devig_proportional(4.10, 1.22)


def test_integer_lines_are_push_aware():
    """Over 3.0 pushes on exactly 3. Reading 1/odds as P(X>3) understates it,
    because the fair price is conditional on no push."""
    mu = 3.2
    raw = 1.0 - poisson(mu).cdf(3)
    conditional = p_over_from_dist(mu, None, 3.0)
    assert conditional > raw
    push = poisson(mu).pmf(3)
    assert conditional == pytest.approx(raw / (1 - push), abs=1e-9)


def test_half_integer_lines_have_no_push():
    mu = 3.2
    assert p_over_from_dist(mu, None, 3.5) == pytest.approx(
        1.0 - poisson(mu).cdf(3), abs=1e-9
    )


def test_fit_recovers_a_known_poisson():
    mu_true = 4.3
    pts = [(l, p_over_from_dist(mu_true, None, l)) for l in (2.5, 3.5, 4.5, 5.5)]
    fit = fit_ladder(pts, family="poisson")
    assert fit.mu == pytest.approx(mu_true, abs=0.02)
    assert fit.rmse < 1e-6


def test_fit_recovers_a_known_negative_binomial():
    mu_true, r_true = 9.8, 12.0
    pts = [(l, p_over_from_dist(mu_true, r_true, l)) for l in (7.5, 8.5, 9.5, 10.5, 11.5)]
    fit = fit_ladder(pts)
    assert fit.mu == pytest.approx(mu_true, abs=0.1)
    assert fit.rmse < 1e-4


def test_fair_odds_inverts_probability():
    fit = LadderFit(mu=3.2, dispersion=None, rmse=0.0, n_lines=4, method="poisson")
    assert fit.fair_odds(3.5) == pytest.approx(1.0 / fit.p_over(3.5))
    assert fit.fair_odds(3.5, "Under") == pytest.approx(1.0 / (1 - fit.p_over(3.5)))


def test_p_over_is_monotonically_decreasing_in_the_line():
    fit = LadderFit(mu=9.7, dispersion=15.0, rmse=0.0, n_lines=3, method="nbinom")
    probs = [fit.p_over(l) for l in (7.5, 8.5, 9.5, 10.5, 11.5, 12.5)]
    assert probs == sorted(probs, reverse=True)


def test_needs_at_least_two_lines():
    with pytest.raises(ValueError):
        fit_ladder([(3.5, 0.4)])


def test_one_sided_lines_are_dropped():
    outcomes = [
        {"name": "Over", "point": 3.5, "price": 2.46},
        {"name": "Under", "point": 3.5, "price": 1.54},
        {"name": "Over", "point": 9.5, "price": 1.67},      # no matching Under
    ]
    assert [l for l, _ in ladder_from_outcomes(outcomes)] == [3.5]


# ── against the real captured Pinnacle ladder ────────────────────────────

@pytest.fixture(scope="module")
def pinnacle():
    d = json.loads(FIXTURE.read_text())
    return {m["key"]: m["outcomes"]
            for b in d["bookmakers"] if b["key"] == "pinnacle"
            for m in b["markets"]}


def test_real_cards_ladder_fits_plausibly(pinnacle):
    fit = fit_ladder(ladder_from_outcomes(pinnacle["alternate_totals_cards"]))
    assert 2.0 < fit.mu < 6.0, "implied cards mean outside any sane range"
    assert fit.rmse < 0.03
    assert fit.n_lines >= 4


def test_real_corners_ladder_fits_plausibly(pinnacle):
    fit = fit_ladder(ladder_from_outcomes(pinnacle["alternate_totals_corners"]))
    assert 7.0 < fit.mu < 14.0
    assert fit.rmse < 0.01


def test_extrapolates_to_lines_the_book_never_quoted(pinnacle):
    """The whole point: 1win may only offer a line Pinnacle does not price."""
    fit = fit_ladder(ladder_from_outcomes(pinnacle["alternate_totals_corners"]))
    quoted = {l for l, _ in ladder_from_outcomes(pinnacle["alternate_totals_corners"])}
    assert 12.5 not in quoted
    p = fit.p_over(12.5)
    assert 0.05 < p < 0.45
    assert fit.fair_odds(12.5) > fit.fair_odds(10.5)
