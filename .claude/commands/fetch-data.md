Refresh all data sources for the winner-recommender project.

Run the following in order:

**1. Historical match data (football-data.co.uk)**
Only needed when a new season starts or adding a new league.
Leagues available: EPL, Bundesliga, Serie_A, La_Liga, Ligue_1, Championship
```
python src/data/fetch_football_data.py
```
Or for a specific league/season:
```python
from src.data.fetch_football_data import download_season, normalise, save_to_db, DB_PATH
import sqlite3
conn = sqlite3.connect(DB_PATH)
df = download_season('La_Liga', 2024)
df = normalise(df)
n = save_to_db(df, conn)
conn.close()
```

**2. Current odds (The Odds API)**
Run every week before generating recommendations.
```
python src/data/fetch_odds.py
```
This fetches odds for all 4 leagues for the next 7 days.
API key is in .env: ODDS_API_KEY

**3. After fetching, generate recommendations**
```
python src/recommend/recommend_today.py
```

**Context:**
- DB is at data/db/winner.db
- Tables: matches_history (historical results + Pinnacle odds), odds_raw (current bookmaker odds)
- Leagues in DB: EPL, Bundesliga, Serie_A, La_Liga (2020–2026)
- The Odds API: free tier = 500 requests/month. Each fetch_odds.py run uses ~20-40 requests.
- football-data.co.uk: completely free, no API key needed
