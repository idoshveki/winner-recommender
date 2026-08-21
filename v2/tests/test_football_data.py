"""Tests for the football-data ingest.

These assert the two corrections to v1 that matter: fouls are captured, and
Pinnacle closing odds are distinct from opening odds.
"""
from __future__ import annotations

import pytest

from v2.ingest.football_data import COLUMN_MAP, parse_csv, season_code

HEADER = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,"
    "HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,PSH,PSD,PSA,PSCH,PSCD,PSCA\n"
)
ROW = (
    "E0,15/08/2025,20:00,Arsenal,Chelsea,2,1,H,1,0,H,M Oliver,"
    "14,9,6,3,11,13,7,4,2,3,0,1,1.85,3.60,4.20,1.80,3.70,4.40\n"
)


def test_season_code():
    assert season_code(2025) == "2526"
    assert season_code(2020) == "2021"
    assert season_code(1999) == "9900"


def test_parses_a_complete_row():
    load = parse_csv(HEADER + ROW, "EPL", 2025)
    assert len(load.rows) == 1
    r = load.rows[0]
    assert r["date"] == "2025-08-15"
    assert (r["home_team"], r["away_team"]) == ("Arsenal", "Chelsea")
    assert (r["home_goals"], r["away_goals"]) == (2, 1)
    assert (r["home_corners"], r["away_corners"]) == (7, 4)
    assert (r["home_yellow"], r["away_yellow"]) == (2, 3)


def test_captures_fouls_which_v1_dropped():
    r = parse_csv(HEADER + ROW, "EPL", 2025).rows[0]
    assert r["home_fouls"] == 11
    assert r["away_fouls"] == 13


def test_captures_referee():
    r = parse_csv(HEADER + ROW, "EPL", 2025).rows[0]
    assert r["referee"] == "M Oliver"


def test_pinnacle_closing_is_distinct_from_opening():
    """v1 mapped PSH (opening) onto `pinnacle_h` and called it the closing
    sharp reference. Both must survive ingest, separately."""
    r = parse_csv(HEADER + ROW, "EPL", 2025).rows[0]
    assert r["pinnacle_open_h"] == 1.85
    assert r["pinnacle_close_h"] == 1.80
    assert r["pinnacle_open_h"] != r["pinnacle_close_h"]


def test_skips_unplayed_fixtures_rather_than_inventing_zeros():
    unplayed = "E0,20/08/2025,20:00,Spurs,Everton,,,,,,,M Oliver,,,,,,,,,,,,,,,,,,\n"
    load = parse_csv(HEADER + unplayed, "EPL", 2025)
    assert load.rows == []
    assert load.skipped == 1


def test_blank_trailing_lines_are_skipped_not_parsed():
    load = parse_csv(HEADER + ROW + ",,,,,,,,,,,,,,,,,,,,,,,,,,,,,\n", "EPL", 2025)
    assert len(load.rows) == 1
    assert load.skipped == 1


def test_missing_numeric_becomes_none_not_zero():
    """A missing corner count must never silently read as 0 - that is what
    produced v1's maximum-confidence phantom picks from empty history."""
    no_corners = ROW.replace(",7,4,2,3,", ",,,2,3,")
    r = parse_csv(HEADER + no_corners, "EPL", 2025).rows[0]
    assert r["home_corners"] is None
    assert r["away_corners"] is None


def test_column_map_has_no_duplicate_targets():
    targets = list(COLUMN_MAP.values())
    assert len(targets) == len(set(targets)), "two csv columns map to one field"


def test_unknown_league_raises():
    from v2.ingest.football_data import fetch_season
    with pytest.raises(KeyError):
        fetch_season("Ligue_1", 2025)
