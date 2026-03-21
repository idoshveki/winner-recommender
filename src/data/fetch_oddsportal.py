"""
Phase 2: Fetch historical BTTS and Over/Under odds from OddsPortal.

FINDINGS:
- OddsPortal does NOT list 1win or Pinnacle as bookmakers.
- OddsPortal does NOT have a YC (Yellow Cards) Over/Under market tab.
- Available bookmakers: bet365, 1xBet, 888sport, Cloudbet, Betsson, etc.
- The scraper extracts BTTS Yes odds and O/U 2.5 goals odds from OddsPortal.
- These serve as REFERENCE odds (not 1win's actual odds).
- The DOM must be navigated by clicking tabs — hash navigation (#bts;2) doesn't
  work reliably with Playwright since OddsPortal uses Vue Router.

Usage:
  source .venv/bin/activate
  playwright install chromium  # first time only
  python src/data/fetch_oddsportal.py
"""

import asyncio
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "db" / "winner.db"

LEAGUE_SLUGS = {
    'EPL':        'football/england/premier-league',
    'La_Liga':    'football/spain/laliga',
    'Bundesliga': 'football/germany/bundesliga',
    'Serie_A':    'football/italy/serie-a',
}

# Bookmakers available on OddsPortal (1win and Pinnacle are NOT listed)
REFERENCE_BOOKS = ['bet365', '888sport', 'Betsson']

BASE_URL = "https://www.oddsportal.com"

# DB team name → fragment to search for in OddsPortal match URLs
# (OddsPortal uses full/different names than our abbreviated DB names)
ODDSPORTAL_SLUG_MAP = {
    # EPL
    "Nott'm Forest": "nottingham",
    "Man United":    "manchester-united",
    "Man City":      "manchester-city",
    "Newcastle":     "newcastle",
    "West Ham":      "west-ham",
    "Wolves":        "wolves",
    "Brighton":      "brighton",
    "Tottenham":     "tottenham",
    # Bundesliga
    "Ein Frankfurt": "frankfurt",
    "M'gladbach":   "gladbach",
    "Bayern Munich": "fc-bayern",
    "Dortmund":      "dortmund",
    "Leverkusen":    "leverkusen",
    "St Pauli":      "st-pauli",
    "FC Koln":       "koln",
    "Hoffenheim":    "hoffenheim",
    "Stuttgart":     "stuttgart",
    "Wolfsburg":     "wolfsburg",
    "Mainz":         "mainz",
    "Werder Bremen": "werder",
    "Augsburg":      "augsburg",
    "Heidenheim":    "heidenheim",
    "RB Leipzig":    "rb-leipzig",
    "Union Berlin":  "union-berlin",
    "Freiburg":      "freiburg",
    # La Liga
    "Ath Madrid":    "atletico",
    "Ath Bilbao":    "athletic",
    "Vallecano":     "rayo-vallecano",
    "Espanol":       "espanyol",
    "Betis":         "betis",
    "Sociedad":      "real-sociedad",
    "Celta":         "celta",
    "Girona":        "girona",
    "Osasuna":       "osasuna",
    "Mallorca":      "mallorca",
    "Levante":       "levante",
    "Oviedo":        "oviedo",
    "Las Palmas":    "las-palmas",
    "Alaves":        "alaves",
    "Getafe":        "getafe",
    "Sevilla":       "sevilla",
    "Valencia":      "valencia",
    "Villarreal":    "villarreal",
    "Elche":         "elche",
    # Serie A
    "Inter":         "inter",
    "Milan":         "ac-milan",
    "Roma":          "roma",
    "Atalanta":      "atalanta",
    "Juventus":      "juventus",
    "Napoli":        "napoli",
    "Lazio":         "lazio",
    "Fiorentina":    "fiorentina",
    "Torino":        "torino",
    "Verona":        "hellas-verona",
    "Parma":         "parma",
    "Monza":         "monza",
    "Bologna":       "bologna",
    "Udinese":       "udinese",
    "Lecce":         "lecce",
    "Genoa":         "genoa",
    "Cagliari":      "cagliari",
    "Empoli":        "empoli",
    "Pisa":          "pisa",
    "Cremonese":     "cremonese",
}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_odds_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date   TEXT NOT NULL,
            home_team    TEXT NOT NULL,
            away_team    TEXT NOT NULL,
            league       TEXT,
            market       TEXT NOT NULL,
            bookmaker    TEXT NOT NULL,
            odds         REAL,
            fetched_at   TEXT,
            UNIQUE(match_date, home_team, away_team, market, bookmaker)
        )
    """)
    conn.commit()
    conn.close()


def team_slug(name):
    """Return the URL fragment to search for in OddsPortal hrefs."""
    if name in ODDSPORTAL_SLUG_MAP:
        return ODDSPORTAL_SLUG_MAP[name]
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def parse_bookmaker_odds(lines, target_books):
    """
    Parse page text lines to extract {bookmaker: [over_odds, under_odds]} pairs.

    OddsPortal structure after clicking a market row:
        bookmaker_name
        CLAIM BONUS
        handicap_value  (e.g. '+2.5')
        over_odds       (e.g. '1.40')
        under_odds      (e.g. '3.00')
        payout%
    """
    result = {}
    i = 0
    while i < len(lines):
        if lines[i] == 'CLAIM BONUS' and i > 0:
            book = lines[i - 1]
            book_match = next((b for b in target_books if b.lower() == book.lower()), None)
            if book_match:
                odds_vals = []
                j = i + 1
                while j < min(len(lines), i + 6):
                    raw = lines[j]
                    # Skip handicap labels like '+2.5', '-1', etc.
                    if not raw.startswith('+') and not raw.startswith('-'):
                        try:
                            v = float(raw)
                            if 1.01 <= v <= 50:
                                odds_vals.append(v)
                        except ValueError:
                            pass
                    j += 1
                if odds_vals:
                    result[book_match] = odds_vals[0]  # first value = Over/Yes odds
        i += 1
    return result


async def find_match_url(page, league_slug, home, away, match_date):
    """
    Find the OddsPortal match URL by paginating through league results pages.
    Tries up to MAX_PAGES pages (each page ~20 matches, so covers ~200 recent matches).
    """
    home_slug = team_slug(home)
    away_slug = team_slug(away)
    results_url = f"{BASE_URL}/{league_slug}/results/"
    MAX_PAGES = 8

    for page_num in range(1, MAX_PAGES + 1):
        url = results_url if page_num == 1 else f"{results_url}#/page/{page_num}/"
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(2000)

            links = await page.query_selector_all(f'a[href*="{home_slug}"]')
            for link in links:
                href = await link.get_attribute('href')
                if href and away_slug in href and re.search(r'-[A-Za-z0-9]{8}/', href):
                    return BASE_URL + href if href.startswith('/') else href

            # Check if this page has any match links at all (stop early if empty)
            all_match_links = await page.query_selector_all(
                f'a[href*="/{league_slug.split("/")[-1]}/"]'
            )
            match_hrefs = [
                await l.get_attribute('href') for l in all_match_links
                if re.search(r'-[A-Za-z0-9]{8}/', await l.get_attribute('href') or '')
            ]
            if not match_hrefs:
                break  # No more results pages

        except Exception as e:
            print(f"    find_match error (page {page_num}): {e}")
            break

    return None


async def find_match_url_upcoming(page, league_slug, home, away):
    """
    Find a match URL from OddsPortal's upcoming fixtures / main league page.
    Used for Phase 3 live odds before email send.
    Checks both the main league page and the fixtures page.
    """
    home_slug = team_slug(home)
    away_slug = team_slug(away)

    for url in [
        f"{BASE_URL}/{league_slug}/",
        f"{BASE_URL}/{league_slug}/fixtures/",
    ]:
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(2000)

            links = await page.query_selector_all(f'a[href*="{home_slug}"]')
            for link in links:
                href = await link.get_attribute('href')
                if href and away_slug in href and re.search(r'-[A-Za-z0-9]{8}/', href):
                    return BASE_URL + href if href.startswith('/') else href
        except Exception:
            pass

    return None


async def _fetch_live_odds_async(picks_list):
    """
    For each BTTS pick, fetch live BTTS Yes + Over/Under 2.5 odds from OddsPortal.
    Returns dict: {match_key: {'btts': odds, 'ou25': odds}} using bet365 as reference.
    match_key = "Home vs Away"
    """
    from playwright.async_api import async_playwright

    result = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()
        await page.route('**/*.{png,jpg,jpeg,gif,svg,woff,woff2}', lambda r: r.abort())

        for pick in picks_list:
            match_key = pick['match']
            league = pick.get('league', '')
            league_slug = LEAGUE_SLUGS.get(league)
            if not league_slug:
                continue

            # "Home vs Away" → split
            parts = match_key.split(' vs ', 1)
            if len(parts) != 2:
                continue
            home, away = parts[0].strip(), parts[1].strip()

            print(f"  [Phase3] Fetching live odds: {match_key}...")
            match_url = await find_match_url_upcoming(page, league_slug, home, away)
            if not match_url:
                print(f"    Not found on OddsPortal upcoming — using assumed odds")
                continue

            print(f"    Found: {match_url}")
            btts = await fetch_btts_odds(page, match_url, REFERENCE_BOOKS)
            ou25 = await fetch_ou_odds(page, match_url, '2.5', REFERENCE_BOOKS)

            ref_btts = btts.get('bet365') or btts.get('888sport') or btts.get('Betsson')
            ref_ou25 = ou25.get('bet365') or ou25.get('888sport') or ou25.get('Betsson')

            if ref_btts and ref_ou25:
                result[match_key] = {'btts': ref_btts, 'ou25': ref_ou25}
                print(f"    BTTS Yes={ref_btts:.2f} O/U2.5={ref_ou25:.2f}")

            await asyncio.sleep(1)

        await browser.close()

    return result


def fetch_live_odds_for_picks(picks_list):
    """
    Synchronous wrapper around _fetch_live_odds_async.
    Call from non-async code (e.g. send_weekly.py).
    Returns {match_key: {'btts': x, 'ou25': y}}.
    """
    import asyncio as _asyncio
    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context — shouldn't happen from send_weekly.py
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(_asyncio.run, _fetch_live_odds_async(picks_list))
                return future.result()
        else:
            return loop.run_until_complete(_fetch_live_odds_async(picks_list))
    except RuntimeError:
        return _asyncio.run(_fetch_live_odds_async(picks_list))


async def click_tab(page, tab_text):
    """Click a market tab by its text content using JS (avoids visibility issues)."""
    await page.evaluate("""
    (tabText) => {
        const links = document.querySelectorAll('a');
        for (const link of links) {
            if (link.textContent.trim() === tabText) { link.click(); return true; }
        }
        return false;
    }
    """, tab_text)
    await page.wait_for_timeout(3000)


async def click_row(page, row_text):
    """Click a specific data row (e.g. 'Over/Under +2.5') to expand bookmaker details."""
    await page.evaluate("""
    (rowText) => {
        const els = document.querySelectorAll('*');
        for (const el of els) {
            if (el.textContent.trim() === rowText && el.children.length === 0) {
                el.click(); return true;
            }
        }
        return false;
    }
    """, row_text)
    await page.wait_for_timeout(3000)


async def fetch_btts_odds(page, match_url, books):
    """Navigate to BTTS tab and extract Yes odds for target bookmakers."""
    await page.goto(match_url, wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(2500)
    await click_tab(page, 'Both Teams to Score')

    text = await page.inner_text('body')
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return parse_bookmaker_odds(lines, books)


async def fetch_ou_odds(page, match_url, line, books):
    """Navigate to Over/Under tab, expand a specific line, extract Over odds."""
    await page.goto(match_url, wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(2500)
    await click_tab(page, 'Over/Under')
    await click_row(page, f'Over/Under +{line}')

    text = await page.inner_text('body')
    lines_text = [l.strip() for l in text.split('\n') if l.strip()]
    return parse_bookmaker_odds(lines_text, books)


async def fetch_match_odds(page, league, home, away, match_date):
    """Return {market: {bookmaker: odds}} for a given historical match."""
    league_slug = LEAGUE_SLUGS.get(league)
    if not league_slug:
        print(f"  Unknown league: {league}")
        return {}

    match_url = await find_match_url(page, league_slug, home, away, match_date)
    if not match_url:
        print(f"  Match not found: {home} vs {away} ({match_date})")
        return {}

    print(f"  Found: {match_url}")

    btts_odds = await fetch_btts_odds(page, match_url, REFERENCE_BOOKS)
    print(f"    BTTS: {btts_odds}")

    ou25_odds = await fetch_ou_odds(page, match_url, '2.5', REFERENCE_BOOKS)
    print(f"    O/U 2.5: {ou25_odds}")

    result = {}
    if btts_odds:
        result['BTTS Yes'] = btts_odds
    if ou25_odds:
        result['Over 2.5 Goals'] = ou25_odds
    return result


def save_odds(match_date, home, away, league, market, book_odds):
    if not book_odds:
        return
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    for bname, odds in book_odds.items():
        conn.execute("""
            INSERT OR REPLACE INTO market_odds_history
                (match_date, home_team, away_team, league, market, bookmaker, odds, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (match_date, home, away, league, market, bname, odds, now))
    conn.commit()
    conn.close()
    print(f"    Saved {market}: {book_odds}")


async def run():
    from playwright.async_api import async_playwright
    import pandas as pd

    init_db()

    conn = sqlite3.connect(DB_PATH)
    history = pd.read_sql("""
        SELECT league, date, home_team, away_team
        FROM matches_history
        WHERE home_goals IS NOT NULL
          AND date >= '2024-01-01'
          AND date < date('now')
        ORDER BY RANDOM()
        LIMIT 40
    """, conn)
    conn.close()

    print(f"Fetching odds for {len(history)} matches...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()
        await page.route('**/*.{png,jpg,jpeg,gif,svg,woff,woff2}', lambda r: r.abort())

        total = len(history)
        for idx, (_, row) in enumerate(history.iterrows()):
            league = row['league']
            home   = row['home_team']
            away   = row['away_team']
            date   = row['date'][:10]
            print(f"\n[{idx+1}/{total}] {home} vs {away} ({league}, {date})")

            odds_result = await fetch_match_odds(page, league, home, away, date)
            for market, book_odds in odds_result.items():
                save_odds(date, home, away, league, market, book_odds)

            await asyncio.sleep(2)

        await browser.close()

    # Summary
    import pandas as pd
    conn = sqlite3.connect(DB_PATH)
    summary = pd.read_sql("""
        SELECT market, bookmaker, COUNT(*) as games,
               ROUND(AVG(odds), 3) as avg_odds,
               ROUND(MIN(odds), 2) as min_odds,
               ROUND(MAX(odds), 2) as max_odds
        FROM market_odds_history
        GROUP BY market, bookmaker
        ORDER BY market, bookmaker
    """, conn)
    conn.close()
    print("\n=== Reference Odds Summary ===")
    print(summary.to_string(index=False))
    print("""
NOTE: These are reference odds from OddsPortal (bet365/888sport/Betsson).
1win and Pinnacle are NOT listed on OddsPortal.
To compute estimated 1win odds: multiply reference odds by ~1.05 (1win typically better).
For YC Over 3.5: not available on OddsPortal. Use assumed 1.50 (validated by 67% hit rate at yc_pred>=4.5).
""")


if __name__ == '__main__':
    asyncio.run(run())
