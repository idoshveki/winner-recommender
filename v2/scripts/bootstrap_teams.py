"""One-time: build the canonical team table + alias map.

Pulls every team from every SofaScore league-season, matches them against the
distinct football-data names in the cached history, and writes a review file.

Anything below AUTO_THRESHOLD is left for a human to confirm ONCE. After that
the mapping lives in the database and runtime resolution is an exact lookup
that raises on a miss - never a fuzzy match, which is how v1 silently dropped
fixtures for months.

    python -m v2.scripts.bootstrap_teams
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from v2.ingest import sofascore
from v2.lib.config import V2
from v2.lib.names import best_match, normalise

AUTO_THRESHOLD = 0.70          # accept without review
REVIEW_THRESHOLD = 0.40        # below this, don't even suggest
CACHE = V2 / ".cache"
OUT = V2 / "db" / "team_map.json"
REVIEW = V2 / "db" / "team_map_review.json"


def sofascore_teams() -> dict:
    """{league: {sofascore_id: {...}}} across all seasons since 2020."""
    cache = CACHE / "sofascore_teams.json"
    if cache.exists():
        return json.loads(cache.read_text())
    out = defaultdict(dict)
    for league, tid, season_id, label in sofascore.iter_league_seasons(2020):
        try:
            teams = sofascore.teams_in_season(tid, season_id)
        except Exception as exc:
            print(f"  !! {league} {label}: {exc}")
            continue
        for t in teams:
            out[league][str(t["sofascore_id"])] = t
        print(f"  {league:11s} {label}  {len(teams):2d} teams")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, indent=2))
    return out


def football_data_names() -> dict:
    """{league: [name, ...]} from the cached CSV history."""
    rows = json.loads((CACHE / "football_data.json").read_text())
    out = defaultdict(set)
    for r in rows:
        out[r["league"]].add(r["home_team"])
        out[r["league"]].add(r["away_team"])
    return {k: sorted(v) for k, v in out.items()}


def main() -> int:
    print("Fetching SofaScore rosters...")
    sofa = sofascore_teams()
    fd = football_data_names()

    mapping, review = {}, []
    for league, names in sorted(fd.items()):
        candidates = [(t["name"], t) for t in sofa.get(league, {}).values()]
        for name in names:
            team, score, matched = best_match(name, candidates, threshold=REVIEW_THRESHOLD)
            entry = {
                "league": league,
                "football_data": name,
                "sofascore_name": matched,
                "sofascore_id": team["sofascore_id"] if team else None,
                "score": round(score, 3),
            }
            if team and score >= AUTO_THRESHOLD:
                mapping[f"{league}|{name}"] = entry
            else:
                review.append(entry)

    # Fold in human-reviewed decisions, which win over anything automatic.
    manual_path = OUT.parent / "team_map_manual.json"
    if manual_path.exists():
        manual = json.loads(manual_path.read_text())
        for key, entry in manual.items():
            if key.startswith("_"):
                continue
            mapping[key] = entry
            review = [r for r in review
                      if f"{r['league']}|{r['football_data']}" != key]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(mapping, indent=2, ensure_ascii=False))
    REVIEW.write_text(json.dumps(review, indent=2, ensure_ascii=False))

    total = sum(len(v) for v in fd.values())
    print(f"\nauto-mapped {len(mapping)}/{total}  ->  {OUT.name}")
    print(f"needs review {len(review)}          ->  {REVIEW.name}")
    for e in review:
        got = f"{e['sofascore_name']} ({e['score']})" if e["sofascore_name"] else "NO CANDIDATE"
        print(f"   {e['league']:11s} {e['football_data']:22s} -> {got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
