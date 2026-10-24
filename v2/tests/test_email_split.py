"""A recommendation for a match already played is worse than no recommendation.

v2 records picks as it finds them on any day; the email goes out on Friday. On
2026-10-23 it led with Levante UD v Athletic Club, played on the Tuesday. The
reader checked, found the match long finished, and lost confidence in the rest
of the email - which is the real cost.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from v2.recommend.send_email import split_by_kickoff

NOW = datetime.now(timezone.utc)


def _p(hours: float):
    return {"kickoff": NOW + timedelta(hours=hours)}


def test_future_kickoffs_are_placeable():
    live, done = split_by_kickoff([_p(2), _p(48)])
    assert len(live) == 2 and not done


def test_past_kickoffs_are_not():
    live, done = split_by_kickoff([_p(-2), _p(-72)])
    assert not live and len(done) == 2


def test_the_levante_case():
    """Recorded Monday, played Tuesday, emailed Friday."""
    live, done = split_by_kickoff([_p(-48), _p(6)])
    assert len(live) == 1 and len(done) == 1


def test_a_pick_without_a_kickoff_is_treated_as_placeable():
    """Unknown is not the same as past. Hiding a pick because its kickoff was
    never recorded would silently drop a real recommendation."""
    live, done = split_by_kickoff([{"kickoff": None}])
    assert len(live) == 1 and not done
