"""Team-name normalisation and matching.

Used ONLY to bootstrap the alias table. At runtime, resolution is an exact
lookup against team_aliases that raises on a miss - never a fuzzy match.
That distinction is the whole design: v1 fuzzy-matched at runtime with
`a in b or b in a`, so "Inter" matched "Inter Milan" and also anything else
containing "inter", and unmapped names fell through unchanged and silently.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Tuple

# Corporate/legal noise that carries no identifying information.
_NOISE = {
    "fc", "cf", "ac", "as", "sc", "ss", "ssc", "sv", "sd", "rc", "rcd", "cd",
    "ud", "afc", "bsc", "vfb", "vfl", "tsg", "fsv", "spvgg", "borussia",
    "deportivo", "real", "club", "calcio",
    "1899", "1846", "1900", "04", "05", "96", "1. ", "de", "the",
    # NOTE: "united", "city", "albion" and "hove" are deliberately NOT noise.
    # They are the only thing separating Manchester United from Manchester
    # City; stripping them made "Man United" score identically against both.
}
# Names where dropping noise words would destroy identity.
_PROTECTED = {"real madrid", "real sociedad", "real betis", "manchester united",
              "manchester city", "west ham united", "newcastle united",
              "leeds united", "sheffield united", "west bromwich albion"}


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


def normalise(name: str) -> str:
    """Lowercase, de-accent, strip punctuation. Reversible enough to debug."""
    s = strip_accents(name or "").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(name: str) -> List[str]:
    n = normalise(name)
    if n in _PROTECTED:
        return n.split()
    return [t for t in n.split() if t not in _NOISE] or n.split()


def _token_score(a: str, b: str) -> float:
    """How much do two single tokens look like the same word?"""
    if a == b:
        return 1.0
    # abbreviation: "ath" -> "atletico", "ein" -> "eintracht"
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return 0.85
    # containment: "gladbach" inside "monchengladbach"
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 5 and short in long_:
        return 0.80
    # shared stem: "wolves" / "wolverhampton", "espanol" / "espanyol"
    common = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        common += 1
    if common >= 4:
        return 0.80
    # spelling variants across languages: "munich" / "munchen"
    if len(a) >= 5 and len(b) >= 5 and common >= 3:
        return 0.70
    # Weak abbreviation. football-data writes "Ath Madrid" for Atletico and
    # "Ath Bilbao" for Athletic - "ath" is a prefix of neither ("atl", "ath").
    # Two shared leading letters on a <=4 char token is only ever enough
    # because the OTHER token (madrid / bilbao) does the discriminating; the
    # bidirectional coverage in similarity() is what keeps this safe.
    if len(short) <= 4 and common >= 2:
        return 0.60
    return 0.0


def _coverage(src, dst) -> float:
    """Mean best-match score for each token in `src` against `dst`."""
    if not src:
        return 0.0
    return sum(max((_token_score(t, o) for o in dst), default=0.0) for t in src) / len(src)


def similarity(a: str, b: str) -> float:
    """0..1 similarity between two club names.

    Scored in both directions deliberately. A one-directional subset test makes
    "Milan" a 95% match for "Inter Milan", because every token of the shorter
    name is present in the longer one - the distinguishing token is exactly the
    one it ignores. Weighting the reverse coverage penalises that.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    cov_short = _coverage(short, long_)
    cov_long = _coverage(long_, short)
    return cov_short * (0.6 + 0.4 * cov_long)


def best_match(
    name: str, candidates: Iterable[Tuple[str, object]], threshold: float = 0.60
) -> Tuple[Optional[object], float, Optional[str]]:
    """Return (payload, score, matched_name) or (None, best_score, None)."""
    best_payload, best_score, best_name = None, 0.0, None
    for cand_name, payload in candidates:
        s = similarity(name, cand_name)
        if s > best_score:
            best_payload, best_score, best_name = payload, s, cand_name
    if best_score >= threshold:
        return best_payload, best_score, best_name
    return None, best_score, None
