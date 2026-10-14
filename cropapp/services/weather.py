"""Live 7-day weather forecast via Open-Meteo (no API key required).

The crop plan itself is built from climate normals; this module adds a short
forward-looking forecast so spray, irrigation and sowing advice can react to the
coming week. Every call degrades gracefully - a network failure returns
{"ok": False} and the UI simply hides the strip.
"""

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

DAILY_VARS = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
)

# WMO weather code -> (English label, Marathi label, bootstrap icon)
WEATHER_CODES = {
    0: ("Clear sky", "निरभ्र आकाश", "bi-sun"),
    1: ("Mainly clear", "बहुतांश साफ", "bi-sun"),
    2: ("Partly cloudy", "अंशतः ढगाळ", "bi-cloud-sun"),
    3: ("Overcast", "ढगाळ", "bi-clouds"),
    45: ("Fog", "धुके", "bi-cloud-fog"),
    48: ("Rime fog", "दवयुक्त धुके", "bi-cloud-fog"),
    51: ("Light drizzle", "हलकी रिमझिम", "bi-cloud-drizzle"),
    53: ("Drizzle", "रिमझिम", "bi-cloud-drizzle"),
    55: ("Heavy drizzle", "जोरदार रिमझिम", "bi-cloud-drizzle"),
    61: ("Light rain", "हलका पाऊस", "bi-cloud-rain"),
    63: ("Rain", "पाऊस", "bi-cloud-rain"),
    65: ("Heavy rain", "जोरदार पाऊस", "bi-cloud-rain-heavy"),
    66: ("Freezing rain", "गारठलेला पाऊस", "bi-cloud-rain"),
    67: ("Freezing rain", "गारठलेला पाऊस", "bi-cloud-rain"),
    71: ("Light snow", "हलका हिमवर्षाव", "bi-cloud-snow"),
    73: ("Snow", "हिमवर्षाव", "bi-cloud-snow"),
    75: ("Heavy snow", "जोरदार हिमवर्षाव", "bi-cloud-snow"),
    80: ("Rain showers", "पावसाच्या सरी", "bi-cloud-rain"),
    81: ("Rain showers", "पावसाच्या सरी", "bi-cloud-rain"),
    82: ("Violent showers", "तीव्र सरी", "bi-cloud-rain-heavy"),
    85: ("Snow showers", "हिमसरी", "bi-cloud-snow"),
    86: ("Snow showers", "हिमसरी", "bi-cloud-snow"),
    95: ("Thunderstorm", "वादळ", "bi-cloud-lightning-rain"),
    96: ("Thunderstorm, hail", "वादळ, गारा", "bi-cloud-lightning-rain"),
    99: ("Thunderstorm, hail", "वादळ, गारा", "bi-cloud-lightning-rain"),
}


def _code_info(code):
    try:
        return WEATHER_CODES.get(int(code), ("—", "—", "bi-cloud"))
    except (TypeError, ValueError):
        return ("—", "—", "bi-cloud")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _advisories(days):
    """Turn the coming week into a few concrete, bilingual field advisories."""
    notes = []
    rain_total = sum(d["precip_mm"] or 0 for d in days[:3])
    wet_days = [d for d in days if (d["precip_mm"] or 0) >= 5]
    hot_days = [d for d in days if (d["temp_max"] or 0) >= 37]
    cold_days = [d for d in days if (d["temp_min"] is not None and d["temp_min"] <= 6)]

    if rain_total >= 25 or len(wet_days) >= 3:
        notes.append({
            "level": "high",
            "title_en": "Wet week ahead", "title_mr": "पुढील आठवडा पावसाळी",
            "detail_en": "Significant rain is forecast. Delay fertilizer top-dressing and spraying, and check field drainage.",
            "detail_mr": "मोठा पाऊस अंदाजित आहे. खताची फवारणी व औषध फवारणी उशिरा करा आणि शेतातील निचरा तपासा.",
        })
    if hot_days:
        notes.append({
            "level": "medium",
            "title_en": "Heat spell", "title_mr": "उष्णतेची लाट",
            "detail_en": f"Up to {int(max(d['temp_max'] for d in hot_days))}\u00b0C expected - irrigate in the evening and mulch to cut evaporation.",
            "detail_mr": f"{int(max(d['temp_max'] for d in hot_days))}\u00b0C पर्यंत तापमान - संध्याकाळी सिंचन करा व बाष्पीभवन कमी करण्यासाठी मल्चिंग करा.",
        })
    if cold_days:
        notes.append({
            "level": "medium",
            "title_en": "Cold nights", "title_mr": "थंड रात्री",
            "detail_en": "Low night temperatures - protect sensitive nurseries and avoid late evening irrigation.",
            "detail_mr": "रात्रीचे तापमान कमी - संवेदनशील रोपांचे संरक्षण करा व उशिरा संध्याकाळी सिंचन टाळा.",
        })
    if not wet_days and all((d["precip_mm"] or 0) < 2 for d in days[:5]):
        notes.append({
            "level": "info",
            "title_en": "Dry spell", "title_mr": "कोरडा कालावधी",
            "detail_en": "Little to no rain expected over the next days - plan irrigation as per the crop plan.",
            "detail_mr": "पुढील काही दिवसांत पाऊस नाही - पीक आराखड्यानुसार सिंचनाचे नियोजन करा.",
        })
    return notes


def forecast(lat, lon, days=7, timeout=10):
    """Return a 7-day Open-Meteo forecast, or {"ok": False, "error": ...} on failure."""
    if lat is None or lon is None:
        return {"ok": False, "error": "location is required"}
    try:
        response = requests.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": ",".join(DAILY_VARS),
                "current": "temperature_2m,relative_humidity_2m,precipitation",
                "timezone": "auto",
                "forecast_days": max(1, min(int(days), 16)),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    out = []
    for index, day in enumerate(times):
        code = (daily.get("weather_code") or [None])[index] if index < len(daily.get("weather_code") or []) else None
        label_en, label_mr, icon = _code_info(code)
        out.append({
            "date": day,
            "code": code,
            "label_en": label_en,
            "label_mr": label_mr,
            "icon": icon,
            "temp_max": _num((daily.get("temperature_2m_max") or [None])[index]),
            "temp_min": _num((daily.get("temperature_2m_min") or [None])[index]),
            "precip_mm": _num((daily.get("precipitation_sum") or [None])[index]),
            "precip_prob": _num((daily.get("precipitation_probability_max") or [None])[index]),
            "wind_max": _num((daily.get("wind_speed_10m_max") or [None])[index]),
        })

    current = payload.get("current") or {}
    return {
        "ok": True,
        "days": out,
        "current": {
            "temp_c": _num(current.get("temperature_2m")),
            "humidity_pct": _num(current.get("relative_humidity_2m")),
            "precip_mm": _num(current.get("precipitation")),
        },
        "advisories": _advisories(out),
        "source": "Open-Meteo",
    }
