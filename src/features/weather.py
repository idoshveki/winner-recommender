"""
Match-day weather via Open-Meteo archive/forecast API (free, no key).
Conditions that matter for football:
  - heavy rain (>5mm)  → fewer goals, more errors
  - strong wind (>40 km/h) → unpredictable play, fewer goals
  - extreme cold (<0°C)  → marginal effect
"""

import requests

# Stadium coordinates for our target teams
# Format: "Team Name": (lat, lon)
STADIUM_COORDS = {
    # Premier League
    "Arsenal": (51.5549, -0.1084),
    "Aston Villa": (52.5092, -1.8847),
    "Bournemouth": (50.7352, -1.8382),
    "Brentford": (51.4882, -0.3087),
    "Brighton and Hove Albion": (50.8618, -0.0834),
    "Chelsea": (51.4816, -0.1909),
    "Crystal Palace": (51.3983, -0.0856),
    "Everton": (53.4388, -2.9662),
    "Fulham": (51.4749, -0.2217),
    "Ipswich Town": (52.0546, 1.1449),
    "Leicester City": (52.6204, -1.1423),
    "Liverpool": (53.4308, -2.9609),
    "Manchester City": (53.4831, -2.2004),
    "Manchester United": (53.4631, -2.2913),
    "Newcastle United": (54.9756, -1.6217),
    "Nottingham Forest": (52.9400, -1.1326),
    "Southampton": (50.9058, -1.3914),
    "Sunderland": (54.9148, -1.3879),
    "Tottenham Hotspur": (51.6043, -0.0661),
    "West Ham United": (51.5386, 0.0165),
    "Wolverhampton Wanderers": (52.5900, -2.1302),
    # La Liga
    "Real Madrid": (40.4531, -3.6883),
    "Barcelona": (41.3809, 2.1228),
    "Atletico Madrid": (40.4361, -3.5994),
    "Atlético Madrid": (40.4361, -3.5994),
    "Sevilla": (37.3840, -5.9705),
    "Valencia": (39.4748, -0.3585),
    "Villarreal": (39.9440, -0.1038),
    "Athletic Club": (43.2641, -2.9494),
    "Real Sociedad": (43.3014, -1.9737),
    "Real Betis": (37.3561, -5.9817),
    "Osasuna": (42.7966, -1.6364),
    "Girona FC": (41.9603, 2.8193),
    "Celta Vigo": (42.2117, -8.7396),
    "Getafe": (40.3237, -3.7167),
    "Rayo Vallecano": (40.3920, -3.6568),
    "Deportivo Alavés": (42.8510, -2.6788),
    "Mallorca": (39.5900, 2.6611),
    "Las Palmas": (28.1002, -15.4536),
    "Leganés": (40.3286, -3.7713),
    "Espanyol": (41.3476, 2.0751),
    "Levante UD": (39.4843, -0.3535),
    # Bundesliga
    "FC Bayern München": (48.2188, 11.6248),
    "Borussia Dortmund": (51.4926, 7.4519),
    "Bayer 04 Leverkusen": (51.0384, 7.0023),
    "RB Leipzig": (51.3457, 12.3484),
    "Eintracht Frankfurt": (50.0688, 8.6456),
    "SC Freiburg": (47.9928, 7.8918),
    "VfL Wolfsburg": (52.4322, 10.8037),
    "Borussia M'gladbach": (51.1747, 6.3854),
    "1. FSV Mainz 05": (49.9843, 8.2244),
    "TSG Hoffenheim": (49.2386, 8.8895),
    "1. FC Union Berlin": (52.4573, 13.5677),
    "VfB Stuttgart": (48.7922, 9.2319),
    "SV Werder Bremen": (53.0668, 8.8375),
    "1. FC Heidenheim": (48.6769, 10.1556),
    "FC St. Pauli": (53.5546, 9.9672),
    "1. FC Köln": (50.9336, 6.8752),
    "Hamburger SV": (53.5876, 9.8983),
    "1. FC Nürnberg": (49.4253, 11.1224),
    "1. FC Union Berlin": (52.4573, 13.5677),
    # Serie A
    "Napoli": (40.8280, 14.1932),
    "Inter": (45.4781, 9.1240),
    "Juventus": (45.1097, 7.6414),
    "Milan": (45.4781, 9.1240),
    "Roma": (41.9341, 12.4547),
    "Lazio": (41.9341, 12.4547),
    "Atalanta": (45.7088, 9.6702),
    "Fiorentina": (43.7808, 11.2822),
    "Bologna": (44.4928, 11.3095),
    "Torino": (45.0408, 7.6505),
    "Udinese": (46.0823, 13.2005),
    "Genoa": (44.4161, 8.9515),
    "Cagliari": (39.2005, 9.1134),
    "Lecce": (40.3508, 18.1764),
    "Hellas Verona": (45.4385, 10.9917),
    "Parma": (44.8042, 10.3429),
    "Sassuolo": (44.5457, 10.7834),
    "Cremonese": (45.1308, 10.0169),
    "Como": (45.8145, 9.0817),
    # Ligue 1
    "Paris Saint-Germain": (48.8414, 2.2530),
    "Olympique de Marseille": (43.2696, 5.3963),
    "Olympique Lyonnais": (45.7653, 4.9822),
    "AS Monaco": (43.7275, 7.4159),
    "Lille": (50.6120, 3.1302),
    "Nice": (43.7053, 7.2590),
    "Rennes": (48.1076, -1.7122),
    "Stade Rennais": (48.1076, -1.7122),
    "Lens": (50.4322, 2.8241),
    "RC Lens": (50.4322, 2.8241),
    "Strasbourg": (48.5600, 7.7526),
    "RC Strasbourg": (48.5600, 7.7526),
    "Nantes": (47.2559, -1.5253),
    "Toulouse": (43.5833, 1.4347),
    "Stade Brestois": (48.4108, -4.4787),
    "Le Havre": (49.4993, 0.1330),
    "Auxerre": (47.7831, 3.5673),
    "Metz": (49.1075, 6.2175),
    "Lorient": (47.7460, -3.3667),
    "Angers": (47.4784, -0.5551),
    "Paris FC": (48.8414, 2.2530),
    # UCL/UEL misc
    "Galatasaray": (41.0680, 29.0108),
    "Bodø/Glimt": (67.2827, 14.4142),
    "Sporting CP": (38.7613, -9.1593),
    "Ferencváros TC": (47.4813, 19.0614),
    "Sporting Braga": (41.5657, -8.4210),
    "KRC Genk": (50.9668, 5.5028),
    "FC Midtjylland": (56.0783, 8.8475),
    "Panathinaikos FC": (37.9838, 23.7275),
    "Aston Villa": (52.5092, -1.8847),
    "VfB Stuttgart": (48.7922, 9.2319),
    "FC Porto": (41.1618, -8.5833),
    # Israeli
    "Maccabi Tel Aviv": (32.0665, 34.7647),
    "Hapoel Beer Sheva": (31.2530, 34.7915),
    "Maccabi Haifa": (32.7940, 34.9896),
}

DEFAULT_COORDS = (48.8566, 2.3522)  # Paris fallback


def get_weather(team_name: str, date: str) -> dict:
    """
    Returns weather for a team's stadium on a given date.
    date: 'YYYY-MM-DD'
    """
    lat, lon = STADIUM_COORDS.get(team_name, DEFAULT_COORDS)

    # Use archive for past dates, forecast for future
    from datetime import datetime, date as date_type
    match_date = datetime.strptime(date, "%Y-%m-%d").date()
    today = datetime.today().date()

    if match_date <= today:
        url = "https://archive-api.open-meteo.com/v1/archive"
    else:
        url = "https://api.open-meteo.com/v1/forecast"

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": date,
        "end_date": date,
        "daily": "precipitation_sum,wind_speed_10m_max,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
    }

    try:
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        d = r.json().get("daily", {})

        rain_mm   = (d.get("precipitation_sum") or [0])[0] or 0
        wind_kmh  = (d.get("wind_speed_10m_max") or [0])[0] or 0
        temp_max  = (d.get("temperature_2m_max") or [15])[0] or 15
        temp_min  = (d.get("temperature_2m_min") or [10])[0] or 10

        return {
            "rain_mm":   round(rain_mm, 1),
            "wind_kmh":  round(wind_kmh, 1),
            "temp_max":  round(temp_max, 1),
            "temp_min":  round(temp_min, 1),
            "heavy_rain": rain_mm > 5,
            "strong_wind": wind_kmh > 40,
            "weather_impact": _impact_label(rain_mm, wind_kmh),
        }
    except Exception as e:
        return {"rain_mm": 0, "wind_kmh": 0, "temp_max": 15, "temp_min": 10,
                "heavy_rain": False, "strong_wind": False, "weather_impact": "unknown"}


def _impact_label(rain, wind):
    if rain > 10 or wind > 50:
        return "high"    # strong suppression of goals/quality play
    if rain > 5 or wind > 40:
        return "medium"
    return "low"
