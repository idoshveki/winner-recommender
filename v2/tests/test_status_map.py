"""SofaScore status vocabulary must map onto what the schema accepts.

SofaScore returns "canceled"; the matches table's check constraint allows
"cancelled". That single letter failed v2-daily five mornings in a row, and in
the same stroke let cancelled fixtures back into the schedule every day -
fixtures.py was filtering on the British spelling too, so a cancelled match was
re-inserted as 'scheduled' and then sat in the past looking exactly like a
result the ingest had failed to fetch.

The constraint values are duplicated here on purpose. If migration 0001 ever
changes them, this test should fail rather than quietly allow a status the
database will reject at 05:15 in the morning.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from v2.ingest.sofascore import STATUS_MAP, canonical_status

SCHEMA_ALLOWED = {"scheduled", "live", "finished", "postponed", "cancelled"}


def test_every_mapped_value_is_accepted_by_the_schema():
    assert set(STATUS_MAP.values()) <= SCHEMA_ALLOWED


def test_mapped_values_match_the_migration():
    """Read the constraint out of the migration rather than trusting memory."""
    sql = (Path(__file__).resolve().parents[1] / "db" / "migrations"
           / "0001_init.sql").read_text()
    clause = re.search(r"check\s*\(status in \(([^)]*)\)\)", sql, re.I)
    assert clause, "could not find the status check constraint in 0001_init.sql"
    declared = set(re.findall(r"'([a-z_]+)'", clause.group(1)))
    assert declared == SCHEMA_ALLOWED, (
        f"migration allows {declared}, this test assumes {SCHEMA_ALLOWED}")


@pytest.mark.parametrize("raw,expected", [
    ("canceled", "cancelled"),      # the spelling that caused the outage
    ("cancelled", "cancelled"),
    ("postponed", "postponed"),
    ("finished", "finished"),
    ("notstarted", "scheduled"),
    ("inprogress", "live"),
    ("interrupted", "live"),
    ("suspended", "live"),
    ("willcontinue", "live"),
    ("deleted", "cancelled"),
    ("removed", "cancelled"),
])
def test_known_statuses(raw, expected):
    assert canonical_status(raw) == expected


@pytest.mark.parametrize("raw", ["Canceled", "  FINISHED ", "PostPoned"])
def test_case_and_whitespace_tolerant(raw):
    assert canonical_status(raw) in SCHEMA_ALLOWED


@pytest.mark.parametrize("raw", ["somethingnew", "", None, "unknown_state"])
def test_unknown_returns_none_rather_than_guessing(raw):
    """None, never a guess. A wrong status silently changes whether a match
    counts as played, which decides whether the freshness assertion fires."""
    assert canonical_status(raw) is None


def test_cancelled_is_not_treated_as_played():
    """The two statuses that mean "never happened" must be distinguishable
    from 'finished', because assert_no_missing_results excludes them."""
    assert canonical_status("canceled") != "finished"
    assert canonical_status("postponed") != "finished"
