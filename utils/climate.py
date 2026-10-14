import calendar
import os
from datetime import date

import pandas as pd
import requests

from utils.geo import DATA_DIR, cache_get, cache_set, match_district

MONTH_KEYS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_IMD_PATH = os.path.join(DATA_DIR, "district wise rainfall normal.csv")
_IMD_ROWS = None


def _imd_rows():
    global _IMD_ROWS
    if _IMD_ROWS is None:
        df = pd.read_csv(_IMD_PATH)
        _IMD_ROWS = [
            {"state": str(s).strip(), "district": str(d).strip(), "row": row}
            for s, d, row in zip(df["STATE_UT_NAME"], df["DISTRICT"], df.to_dict("records"))
        ]
    return _IMD_ROWS


def imd_rainfall_normal(district, state, month):
    """IMD district-wise rainfall normal (mm) for the given month (1-12)."""
    if not district or not month:
        return None
    month = int(month)
    if not 1 <= month <= 12:
        return None
    match = match_district(district, state, _imd_rows())
    if not match:
        return _state_average(district, state, month)
    key = MONTH_KEYS[month - 1]
    try:
        return {
            "value_mm": float(match["row"][key]),
            "annual_mm": float(match["row"]["ANNUAL"]),
            "matched_district": match["district"],
            "matched_state": match["state"],
            "source": "IMD District Rainfall Normal 1951-2000 (bundled gov dataset)",
        }
    except (KeyError, TypeError, ValueError):
        return None


def _state_average(district, state, month):
    """District missing from the IMD normals list (created after 1951-2000 normal period)."""
    from utils.geo import states_compatible

    rows = [r for r in _imd_rows() if state and states_compatible(r["state"], state)]
    if not rows:
        return None
    key = MONTH_KEYS[month - 1]
    values, annual = [], []
    for row in rows:
        try:
            values.append(float(row["row"][key]))
            annual.append(float(row["row"]["ANNUAL"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not values:
        return None
    return {
        "value_mm": round(sum(values) / len(values), 1),
        "annual_mm": round(sum(annual) / len(annual), 1) if annual else None,
        "matched_district": None,
        "matched_state": state,
        "is_state_average": True,
        "source": f"IMD state average of {len(values)} districts (district '{district}' is not in the normals list)",
    }


def _month_ranges(month, years=3):
    today = date.today()
    ranges = []
    for back in range(years):
        year = today.year - back
        try:
            start = date(year, month, 1)
        except ValueError:
            continue
        last_day = calendar.monthrange(year, month)[1]
        end = date(year, month, last_day)
        if end >= today:
            if start >= today:
                continue
            end = today
        ranges.append((start.isoformat(), end.isoformat()))
    return ranges


def open_meteo_monthly(lat, lon, month):
    """Multi-year average of temperature, humidity and rainfall for one calendar month at a point."""
    month = int(month)
    key = f"openmeteo|{round(float(lat), 4)}|{round(float(lon), 4)}|{month}"
    cached = cache_get(key, max_age_hours=24 * 30)
    if cached is not None:
        return cached

    temps, hums, rains = [], [], []
    for start, end in _month_ranges(month, years=3):
        try:
            resp = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": start,
                    "end_date": end,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                    "hourly": "relative_humidity_2m",
                    "timezone": "Asia/Kolkata",
                },
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError):
            continue
        daily = payload.get("daily", {})
        pairs = [
            (tmax, tmin, rain)
            for tmax, tmin, rain in zip(
                daily.get("temperature_2m_max", []), daily.get("temperature_2m_min", []), daily.get("precipitation_sum", [])
            )
            if tmax is not None and tmin is not None
        ]
        if pairs:
            temps.append(sum((tmax + tmin) / 2 for tmax, tmin, _ in pairs) / len(pairs))
        if daily.get("precipitation_sum"):
            vals = [v for v in daily["precipitation_sum"] if v is not None]
            if vals:
                rains.append(sum(vals))
        humidity = [v for v in (payload.get("hourly", {}).get("relative_humidity_2m") or []) if v is not None]
        if humidity:
            hums.append(sum(humidity) / len(humidity))

    if not (temps or rains or hums):
        return None
    result = {
        "month": month,
        "temp_c": round(sum(temps) / len(temps), 1) if temps else None,
        "humidity_pct": round(sum(hums) / len(hums), 1) if hums else None,
        "rainfall_mm": round(sum(rains) / len(rains), 1) if rains else None,
        "years_used": len(temps) or len(rains) or len(hums),
        "source": "Open-Meteo historical archive (point-specific, multi-year monthly mean)",
    }
    cache_set(key, result)
    return result


def climate_for_point(lat, lon, month=None, district=None, state=None):
    month = int(month) if month else date.today().month
    point = open_meteo_monthly(lat, lon, month) or {}
    normal = imd_rainfall_normal(district, state, month) if district else None
    return {
        "month": month,
        "temp_c": point.get("temp_c"),
        "humidity_pct": point.get("humidity_pct"),
        "rainfall_actual_mm": point.get("rainfall_mm"),
        "rainfall_normal_mm": normal["value_mm"] if normal else None,
        "sources": {
            "temperature": point.get("source"),
            "humidity": point.get("source"),
            "rainfall_actual": point.get("source"),
            "rainfall_normal": normal["source"] if normal else None,
        },
        "district_matched": normal["matched_district"] if normal else None,
        "rainfall_normal_is_state_average": bool(normal and normal.get("is_state_average")),
    }
