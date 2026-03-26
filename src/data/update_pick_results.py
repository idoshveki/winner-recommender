"""
Auto-fill weekly_picks results from matches_history.
Falls back to livescore.com scrape when football-data.co.uk hasn't updated yet.
For YC markets, scrapes the livescore stats page to get yellow card counts.

Supports markets: H/A, YC Over 3.5, O2.5+BTTS, Draw (D)
"""

import re
import sqlite3
import requests
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "db" / "winner.db"

LIVESCORE_LEAGUE_IDS = {'EPL': '65', 'La_Liga': '75', 'Bundesliga': '67', 'Serie_A': '77'}
LIVESCORE_LEAGUE_SLUGS = {
    'EPL':        ('england', 'premier-league'),
    'La_Liga':    ('spain',   'laliga'),
    'Bundesliga': ('germany', 'bundesliga'),
    'Serie_A':    ('italy',   'serie-a'),
}

_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
_livescore_cache = {}   # date_str -> list of result dicts (includes eid, league)


def _to_slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def _fetch_livescore(date_str):
    if date_str in _livescore_cache:
        return _livescore_cache[date_str]
    try:
        r = requests.get(
            f'https://prod-public-api.livescore.com/v1/api/app/date/soccer/{date_str}/0?locale=en&MD=1',
            headers=_HEADERS, timeout=10
        )
        if r.status_code != 200:
            return []
        results = []
        for stage in r.json().get('Stages', []):
            comp_id = stage.get('CompId', '')
            league = next((l for l, lid in LIVESCORE_LEAGUE_IDS.items() if comp_id == lid), None)
            if not league:
                continue
            for event in stage.get('Events', []):
                hg = event.get('Tr1', '')
                ag = event.get('Tr2', '')
                if hg == '' or ag == '':
                    continue
                hg, ag = int(hg), int(ag)
                results.append({
                    'eid':    event.get('Eid', ''),
                    'league': league,
                    'home':   event.get('T1', [{}])[0].get('Nm', ''),
                    'away':   event.get('T2', [{}])[0].get('Nm', ''),
                    'hg': hg, 'ag': ag,
                    'result': 'H' if hg > ag else ('A' if ag > hg else 'D'),
                })
        _livescore_cache[date_str] = results
        return results
    except Exception as e:
        print(f"  Livescore list fetch failed for {date_str}: {e}")
        return []


def _get_yc_from_stats_page(eid, league, home, away):
    """Scrape livescore stats page for yellow card counts."""
    slugs = LIVESCORE_LEAGUE_SLUGS.get(league)
    if not slugs:
        return None, None
    country, league_slug = slugs
    match_slug = f"{_to_slug(home)}-vs-{_to_slug(away)}"
    url = f"https://www.livescore.com/en/football/{country}/{league_slug}/{match_slug}/{eid}/stats/"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=10)
        if r.status_code != 200:
            return None, None
        m = re.search(
            r'data-id="yellowCards_mtc-dtl-stat"[^>]*>.*?<span[^>]*>(\d+)</span>.*?<span[^>]*>(\d+)</span>',
            r.text, re.DOTALL
        )
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception as e:
        print(f"  YC stats page failed for {match_slug}: {e}")
    return None, None


def _fuzzy_match(a, b):
    a, b = a.lower().strip(), b.lower().strip()
    return a == b or a in b or b in a


def _resolve_from_livescore(home, away, market, pick, week_start, week_end):
    start = datetime.strptime(week_start, '%Y-%m-%d')
    end   = datetime.strptime(week_end,   '%Y-%m-%d')
    d = start
    while d <= end:
        date_str = d.strftime('%Y%m%d')
        for r in _fetch_livescore(date_str):
            if _fuzzy_match(home, r['home']) and _fuzzy_match(away, r['away']):
                if market == 'H/A':
                    if pick == 'H': return 1 if r['result'] == 'H' else 0
                    if pick == 'A': return 1 if r['result'] == 'A' else 0
                    if pick == 'D': return 1 if r['result'] == 'D' else 0

                if market == 'YC Over 3.5':
                    yc_h, yc_a = _get_yc_from_stats_page(r['eid'], r['league'], r['home'], r['away'])
                    if yc_h is None:
                        print(f"    Could not get YC for {r['home']} vs {r['away']}")
                        return None
                    total = yc_h + yc_a
                    print(f"    YC from livescore stats: {r['home']} {yc_h} / {r['away']} {yc_a} = {total}")
                    return 1 if total > 3.5 else 0

                if market == 'O2.5+BTTS':
                    return 1 if (r['hg'] + r['ag'] > 2.5 and r['hg'] > 0 and r['ag'] > 0) else 0
        d += timedelta(days=1)
    return None


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

    if row is not None:
        result, hg, ag, hy, ay = row
        if market == 'H/A':
            if pick == 'H': return 1 if result == 'H' else 0
            if pick == 'A': return 1 if result == 'A' else 0
            if pick == 'D': return 1 if result == 'D' else 0
        if market == 'YC Over 3.5':
            if hy is None or ay is None:
                return None
            return 1 if (hy + ay) > 3.5 else 0
        if market == 'O2.5+BTTS':
            if hg is None or ag is None:
                return None
            return 1 if (hg + ag > 2.5 and hg > 0 and ag > 0) else 0

    # Fallback: livescore
    print(f"    DB miss for '{home} vs {away}' ({market}) — trying livescore...")
    return _resolve_from_livescore(home, away, market, pick, week_start, week_end)


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

        active_legs = []
        if l1_match: active_legs.append(l1_hit)
        if l2_match: active_legs.append(l2_hit)

        # Skip weeks with no legs (blank weeks)
        if not active_legs:
            print(f"  {week}: no legs — skipping")
            continue

        # If any leg is a definitive miss, slip is lost — no need to wait for others
        if any(h == 0 for h in active_legs):
            slip_won = 0
        elif any(h is None for h in active_legs):
            print(f"  {week}: still waiting for match data")
            continue
        else:
            slip_won = 1

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
