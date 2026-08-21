from v2.lib.names import best_match, normalise, similarity, tokens


def test_normalise_strips_accents_and_punctuation():
    assert normalise("Atlético Madrid") == "atletico madrid"
    assert normalise("M'gladbach") == "m gladbach"
    assert normalise("Brighton & Hove Albion") == "brighton and hove albion"


def test_protected_names_keep_their_qualifiers():
    """Dropping 'real' or 'united' would collide Real Sociedad with Sociedad
    and Manchester United with Manchester City."""
    assert "real" in tokens("Real Madrid")
    assert "united" in tokens("Manchester United")
    assert similarity("Manchester United", "Manchester City") < 0.6


def test_matches_known_hard_pairs():
    pairs = [
        ("Ath Madrid", "Atlético Madrid"),
        ("M'gladbach", "Borussia Monchengladbach"),
        ("Ein Frankfurt", "Eintracht Frankfurt"),
        ("Nott'm Forest", "Nottingham Forest"),
        ("Wolves", "Wolverhampton Wanderers"),
        ("Ath Bilbao", "Athletic Club"),
        ("Sociedad", "Real Sociedad"),
        ("Leverkusen", "Bayer 04 Leverkusen"),
        ("Mainz", "1. FSV Mainz 05"),
        ("Espanol", "Espanyol"),
    ]
    for fd_name, sofa_name in pairs:
        assert similarity(fd_name, sofa_name) >= 0.55, f"{fd_name} !~ {sofa_name}"


def test_does_not_match_genuinely_different_clubs():
    assert similarity("Inter", "Internacional") < 0.9
    assert similarity("Milan", "Inter Milan") < 0.9
    assert similarity("Real Madrid", "Real Betis") < 0.6


def test_best_match_returns_none_below_threshold():
    payload, score, name = best_match("Total Nonsense FC", [("Arsenal", 42)])
    assert payload is None and name is None
