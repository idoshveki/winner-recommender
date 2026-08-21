"""Integrity of the bootstrapped team map.

The map is committed, so these run in CI as a regression guard: if a future
bootstrap collapses two clubs onto one id, the build fails rather than the
model quietly training on merged histories.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

MAP_PATH = Path(__file__).resolve().parents[1] / "db" / "team_map.json"


@pytest.fixture(scope="module")
def team_map():
    if not MAP_PATH.exists():
        pytest.skip("run python -m v2.scripts.bootstrap_teams first")
    return json.loads(MAP_PATH.read_text())


def test_every_entry_has_a_sofascore_id(team_map):
    missing = [k for k, v in team_map.items() if not v.get("sofascore_id")]
    assert missing == [], f"unmapped: {missing}"


def test_no_two_clubs_share_one_sofascore_id(team_map):
    """The Manchester failure mode: 'Man United' and 'Man City' both scoring
    identically against 'Manchester City' and collapsing into one club."""
    by_id = defaultdict(list)
    for key, entry in team_map.items():
        by_id[(entry["league"], entry["sofascore_id"])].append(entry["football_data"])
    collisions = {k: v for k, v in by_id.items() if len(v) > 1}
    assert not collisions, f"multiple clubs mapped to one id: {collisions}"


def test_manchester_clubs_are_distinct(team_map):
    united = team_map["EPL|Man United"]
    city = team_map["EPL|Man City"]
    assert united["sofascore_id"] != city["sofascore_id"]
    assert "United" in united["sofascore_name"]
    assert "City" in city["sofascore_name"]


def test_milan_clubs_are_distinct(team_map):
    inter = team_map["Serie_A|Inter"]
    milan = team_map["Serie_A|Milan"]
    assert inter["sofascore_id"] != milan["sofascore_id"]


def test_covers_every_name_in_the_history():
    cache = MAP_PATH.parent.parent / ".cache" / "football_data.json"
    if not cache.exists():
        pytest.skip("no cached history")
    team_map = json.loads(MAP_PATH.read_text())
    rows = json.loads(cache.read_text())
    needed = set()
    for r in rows:
        needed.add(f"{r['league']}|{r['home_team']}")
        needed.add(f"{r['league']}|{r['away_team']}")
    assert needed - set(team_map) == set(), "history contains unmapped teams"
