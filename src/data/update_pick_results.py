"""
Auto-fill weekly_picks results from matches_history.
Run after fetch_football_data.py so results are in the DB.

Supports markets: H/A, YC Over 3.5, O2.5+BTTS, Draw (D)
"""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "db" / "winner.db"


def resolve_hit(conn, match_str, market, pick, week_start, week_end):
    """Return 1 (hit), 0 (miss), or None (match not found yet)."""
    parts = match_str.split(' vs ')
    if len(parts) != 2:
        return None
    home, away = parts[0].strip(), parts[1].strip()

    row = conn.execute("""
        SELECT result, home_goals, away_goals, home_yellow, away_yellow
        FROM matches_history
        WHERE home_team = ? AND away_team = ?
          AND date >= ? AND date <= ?
        LIMIT 1
    """, (home, away, week_start, week_end)).fetchone()

    if row is None:
        return None  # not in DB yet

    result, hg, ag, hy, ay = row

    if market == 'H/A':
        if pick == 'H': return 1 if result == 'H' else 0
        if pick == 'A': return 1 if result == 'A' else 0
        if pick == 'D': return 1 if result == 'D' else 0

    if market == 'YC Over 3.5':
        if hy is None or ay is None:
            return None  # no YC data yet
        return 1 if (hy + ay) > 3.5 else 0

    if market == 'O2.5+BTTS':
        if hg is None or ag is None:
            return None
        return 1 if (hg + ag > 2.5 and hg > 0 and ag > 0) else 0

    return None


def update_results():
    conn = sqlite3.connect(DB_PATH)

    pending = conn.execute("""
        SELECT id, week, leg1_market, leg1_match, leg1_pick,
               leg2_market, leg2_match, leg2_pick,
               draw_match
        FROM weekly_picks
        WHERE slip_won IS NULL
    """).fetchall()

    print(f"Pending weeks: {len(pending)}")
    updated = 0

    for row in pending:
        (pk_id, week, l1_mkt, l1_match, l1_pick,
         l2_mkt, l2_match, l2_pick, draw_match) = row

        week_start, week_end = week.split('/')

        l1_hit = resolve_hit(conn, l1_match, l1_mkt, l1_pick, week_start, week_end) if l1_match else None
        l2_hit = resolve_hit(conn, l2_match, l2_mkt, l2_pick, week_start, week_end) if l2_match else None
        d_hit  = resolve_hit(conn, draw_match, 'H/A', 'D', week_start, week_end) if draw_match else None

        # Only mark slip_won if all legs with matches have resolved
        legs = [h for h in [l1_hit, l2_hit] if l2_match or True]
        active_legs = []
        if l1_match: active_legs.append(l1_hit)
        if l2_match: active_legs.append(l2_hit)

        if any(h is None for h in active_legs):
            print(f"  {week}: still waiting for match data")
            continue

        slip_won = 1 if all(h == 1 for h in active_legs) else 0

        conn.execute("""
            UPDATE weekly_picks
            SET leg1_hit=?, leg2_hit=?, draw_hit=?, slip_won=?
            WHERE id=?
        """, (l1_hit, l2_hit, d_hit, slip_won, pk_id))

        status = "WON" if slip_won else "LOST"
        print(f"  {week}: {status} (leg1={l1_hit} leg2={l2_hit} draw={d_hit})")
        updated += 1

    conn.commit()
    conn.close()
    print(f"Updated {updated} weeks.")


if __name__ == "__main__":
    update_results()
