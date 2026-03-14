"""
Team news sentiment via Google News RSS (free, no key required).
Scans recent headlines for keywords that signal problems or momentum.
"""

import time
import requests
from datetime import datetime, timedelta
from urllib.parse import quote

NEGATIVE_KEYWORDS = [
    "injured", "injury", "out", "ruled out", "suspended", "ban", "sacked",
    "crisis", "chaos", "doubt", "unavailable", "strain", "surgery", "ill",
    "suspended", "red card", "dismissal", "protest", "unrest", "dressing room",
]

POSITIVE_KEYWORDS = [
    "unbeaten", "record", "fit", "returns", "back", "confident", "clinical",
    "dominant", "momentum", "on fire", "impressive", "masterclass",
]


def get_team_news(team_name: str, date: str, days_back: int = 5) -> dict:
    """
    Fetch last `days_back` days of news for a team before `date`.
    Returns sentiment summary.
    """
    query = quote(f"{team_name} football")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-GB&gl=GB&ceid=GB:en"

    cutoff = datetime.strptime(date, "%Y-%m-%d")
    since  = cutoff - timedelta(days=days_back)

    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception:
        return _empty_news(team_name)

    # Parse RSS manually (avoid lxml dependency)
    import re
    items = re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL)

    headlines = []
    neg_count = pos_count = 0

    for item in items:
        title_m = re.search(r"<title>(.*?)</title>", item)
        date_m  = re.search(r"<pubDate>(.*?)</pubDate>", item)
        if not title_m:
            continue

        title = title_m.group(1).lower()

        # Try to filter by date
        if date_m:
            try:
                pub = datetime.strptime(date_m.group(1)[:25], "%a, %d %b %Y %H:%M:%S")
                if pub < since or pub >= cutoff:
                    continue
            except Exception:
                pass

        headlines.append(title)
        for kw in NEGATIVE_KEYWORDS:
            if kw in title:
                neg_count += 1
                break
        for kw in POSITIVE_KEYWORDS:
            if kw in title:
                pos_count += 1
                break

    sentiment = "neutral"
    if neg_count > pos_count + 1:
        sentiment = "negative"
    elif pos_count > neg_count + 1:
        sentiment = "positive"

    # Extract key injury/suspension mentions
    injury_mentions = [h for h in headlines if any(k in h for k in ["injur", "ruled out", "suspended", "doubt"])]

    return {
        "team": team_name,
        "headlines_scanned": len(headlines),
        "negative_signals": neg_count,
        "positive_signals": pos_count,
        "sentiment": sentiment,
        "injury_mentions": injury_mentions[:3],  # top 3
        "sentiment_score": pos_count - neg_count,  # positive = good news
    }


def _empty_news(team_name):
    return {
        "team": team_name,
        "headlines_scanned": 0,
        "negative_signals": 0,
        "positive_signals": 0,
        "sentiment": "unknown",
        "injury_mentions": [],
        "sentiment_score": 0,
    }
