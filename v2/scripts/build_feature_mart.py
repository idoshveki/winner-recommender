"""Populate team_match_form and match_features.

Everything is as-of the moment before kickoff. Rolling stores are updated only
AFTER a row is emitted, so a match can never see itself or anything later.

    python -m v2.scripts.build_feature_mart
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional

from v2.lib.jobs import connect, job


def mean(xs) -> Optional[float]:
    xs = list(xs)
    return round(sum(xs) / len(xs), 2) if xs else None


class TeamState:
    """Rolling history for a team. Card/foul/corner averages carry ACROSS
    seasons; only the league-table fields reset.

    Keying the whole object by season was wrong: it left every team with no
    card history for the first five rounds, so cards_pred was 0 in August and
    every side looked out of form on three games played."""

    def __init__(self):
        self.played = 0
        self.points = 0
        self.gf = 0
        self.ga = 0
        self.results: deque = deque(maxlen=10)      # 'W'/'D'/'L', newest last
        self.pts_hist: deque = deque(maxlen=10)
        self.cards: deque = deque(maxlen=10)
        self.corners: deque = deque(maxlen=10)
        self.fouls: deque = deque(maxlen=10)
        self.shots: deque = deque(maxlen=10)
        self.gf_hist: deque = deque(maxlen=10)
        self.ga_hist: deque = deque(maxlen=10)
        self.venue: Dict[str, Dict[str, deque]] = {
            v: {k: deque(maxlen=5) for k in
                ("cards", "corners", "points", "gf", "ga", "cards_against")}
            for v in ("home", "away")
        }
        self.last_date: Optional[str] = None

    def streaks(self):
        w = l = unb = winless = 0
        for r in reversed(self.results):
            if r == "W":
                if l == 0 and winless == 0: w += 1
                else: break
            else: break
        for r in reversed(self.results):
            if r == "L":
                if w == 0 and unb == 0: l += 1
                else: break
            else: break
        for r in reversed(self.results):
            if r in ("W", "D"): unb += 1
            else: break
        for r in reversed(self.results):
            if r in ("L", "D"): winless += 1
            else: break
        return w, l, unb, winless

    def snapshot(self, venue: str, date: str) -> dict:
        w, l, unb, winless = self.streaks()
        v = self.venue[venue]
        rest = None
        if self.last_date:
            rest = (datetime.fromisoformat(date) - datetime.fromisoformat(self.last_date)).days
        return dict(
            played=self.played, points=self.points, goals_for=self.gf, goals_against=self.ga,
            l5_points=sum(list(self.pts_hist)[-5:]) or 0,
            l10_points=sum(self.pts_hist) or 0,
            l5_goals_for=mean(list(self.gf_hist)[-5:]),
            l5_goals_against=mean(list(self.ga_hist)[-5:]),
            l5_cards=mean(list(self.cards)[-5:]), l10_cards=mean(self.cards),
            l5_corners=mean(list(self.corners)[-5:]), l10_corners=mean(self.corners),
            l5_fouls=mean(list(self.fouls)[-5:]), l5_shots=mean(list(self.shots)[-5:]),
            venue_l5_cards=mean(v["cards"]), venue_l5_corners=mean(v["corners"]),
            venue_l5_points=sum(v["points"]) or 0,
            venue_l5_goals_for=mean(v["gf"]), venue_l5_goals_against=mean(v["ga"]),
            venue_l5_cards_against=mean(v["cards_against"]),
            win_streak=w, loss_streak=l, unbeaten_streak=unb, winless_streak=winless,
            last5="".join(reversed(list(self.results)[-5:])) or None,
            days_rest=min(rest, 60) if rest is not None else None,
        )

    def new_season(self):
        """Reset only what is season-specific. Rolling form persists."""
        self.played = 0
        self.points = 0
        self.gf = 0
        self.ga = 0

    def update(self, venue, pts, gf, ga, cards, cards_against, corners, fouls, shots, date):
        self.played += 1
        self.points += pts
        self.gf += gf
        self.ga += ga
        self.results.append("W" if pts == 3 else ("D" if pts == 1 else "L"))
        self.pts_hist.append(pts)
        self.gf_hist.append(gf); self.ga_hist.append(ga)
        if cards is not None: self.cards.append(cards)
        if corners is not None: self.corners.append(corners)
        if fouls is not None: self.fouls.append(fouls)
        if shots is not None: self.shots.append(shots)
        v = self.venue[venue]
        if cards is not None: v["cards"].append(cards)
        if cards_against is not None: v["cards_against"].append(cards_against)
        if corners is not None: v["corners"].append(corners)
        v["points"].append(pts); v["gf"].append(gf); v["ga"].append(ga)
        self.last_date = date


def main() -> int:
    with job("build-feature-mart") as jr, connect() as conn:
        conn.autocommit = False
        rows = conn.execute("""
            select m.id, m.league, m.season, m.kickoff_utc, m.home_team_id, m.away_team_id,
                   s.home_goals, s.away_goals, s.home_yellow, s.away_yellow,
                   s.home_red, s.away_red, s.home_corners, s.away_corners,
                   s.home_fouls, s.away_fouls, s.home_shots, s.away_shots
            from matches m join match_stats s on s.match_id = m.id
            where m.status='finished' and s.home_goals is not null
            order by m.kickoff_utc, m.id""").fetchall()
        print(f"  {len(rows)} matches")

        state: Dict[tuple, TeamState] = defaultdict(TeamState)   # (league, team)
        seen_season: Dict[tuple, int] = {}
        standings: Dict[tuple, Dict[int, int]] = defaultdict(dict)   # (lg,season) -> team: pts
        form_rows, feat_rows = [], []

        for r in rows:
            (mid, lg, season, ko, ht, at, hg, ag, hy, ay, hr, ar,
             hc, ac, hf, af, hs, a_s) = r
            date = ko.date().isoformat()
            key = lambda t: (lg, t)
            for t in (ht, at):
                if seen_season.get((lg, t)) != season:
                    if (lg, t) in seen_season:
                        state[(lg, t)].new_season()
                    seen_season[(lg, t)] = season

            # league position as of now, from the running table
            table = standings[(lg, season)]
            ranked = sorted(table.items(), key=lambda kv: -kv[1])
            pos = {t: i + 1 for i, (t, _) in enumerate(ranked)}

            snaps = {}
            for team, opp, venue in ((ht, at, "home"), (at, ht, "away")):
                st = state[key(team)]
                snap = st.snapshot(venue, date)
                snap.update(match_id=mid, team_id=team, venue=venue, opponent_id=opp,
                            position=pos.get(team))
                snaps[venue] = snap
                form_rows.append(snap)

            h, a = snaps["home"], snaps["away"]
            cards_book = (hy + ay + 2 * ((hr or 0) + (ar or 0))) if hy is not None else None
            n_played = max(h["played"], a["played"])
            feat_rows.append(dict(
                match_id=mid, league=lg, season=season, kickoff_utc=ko,
                round_num=n_played + 1, season_pct=round(min(n_played / 38.0, 1.0), 3),
                month=ko.month, home_team_id=ht, away_team_id=at,
                cards_pred=(h["venue_l5_cards"] + a["venue_l5_cards"])
                           if h["venue_l5_cards"] is not None and a["venue_l5_cards"] is not None else None,
                corners_pred=(h["venue_l5_corners"] + a["venue_l5_corners"])
                             if h["venue_l5_corners"] is not None and a["venue_l5_corners"] is not None else None,
                goals_pred=(h["venue_l5_goals_for"] + a["venue_l5_goals_for"])
                           if h["venue_l5_goals_for"] is not None and a["venue_l5_goals_for"] is not None else None,
                fouls_pred=(h["l5_fouls"] + a["l5_fouls"])
                           if h["l5_fouls"] is not None and a["l5_fouls"] is not None else None,
                position_diff=(h["position"] - a["position"])
                              if h["position"] and a["position"] else None,
                points_diff=h["points"] - a["points"],
                form_diff=h["l5_points"] - a["l5_points"],
                both_top6=bool(h["position"] and a["position"] and h["position"] <= 6 and a["position"] <= 6),
                both_bottom6=bool(h["position"] and a["position"] and h["position"] >= 15 and a["position"] >= 15),
                home_favourite=None,
                total_cards_book=cards_book,
                total_yellows=(hy + ay) if hy is not None else None,
                total_corners=(hc + ac) if hc is not None else None,
                total_goals=hg + ag,
                result="H" if hg > ag else ("A" if ag > hg else "D"),
                btts=bool(hg > 0 and ag > 0),
            ))

            # ── update AFTER emitting ────────────────────────────────────
            hp = 3 if hg > ag else (1 if hg == ag else 0)
            ap = 3 if ag > hg else (1 if hg == ag else 0)
            state[key(ht)].update("home", hp, hg, ag, hy, ay, hc, hf, hs, date)
            state[key(at)].update("away", ap, ag, hg, ay, hy, ac, af, a_s, date)
            table[ht] = table.get(ht, 0) + hp
            table[at] = table.get(at, 0) + ap

        print(f"  writing {len(form_rows)} form rows, {len(feat_rows)} match rows")
        conn.execute("truncate team_match_form, match_features")
        fcols = list(form_rows[0].keys())
        with conn.cursor().copy(
                f"copy team_match_form ({', '.join(fcols)}) from stdin") as cp:
            for row in form_rows:
                cp.write_row([row[c] for c in fcols])
        mcols = list(feat_rows[0].keys())
        with conn.cursor().copy(
                f"copy match_features ({', '.join(mcols)}) from stdin") as cp:
            for row in feat_rows:
                cp.write_row([row[c] for c in mcols])
        conn.commit()
        jr.add(len(feat_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
