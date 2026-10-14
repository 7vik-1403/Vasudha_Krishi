"""Indicative irrigation planner.

Estimates a crop's seasonal water need, the number of irrigation events across its
duration and a rough cost, and lets you compare water demand across crops. The
numbers are approximate planning aids built from bundled crop water data and the
farmer's declared source - they are not field measurements or scientific
predictions, and the plan says so.
"""

from cropapp.services.datasets import calendar_for, water_for
from cropapp.services.economics import irrigation_rate
from cropapp.services.units import ACRES_PER_HA

DEFAULT_DURATION_DAYS = 100


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def requirement(crop, calendar=None):
    data = water_for(crop) or {}
    calendar = calendar or calendar_for(crop) or {}
    return {
        "season_mm": _num(data.get("season_mm")),
        "need": data.get("need"),
        "interval_days": _num(data.get("interval_days")),
        "duration_days": _num(calendar.get("duration_days")),
    }


def plan(crop, water=None, calendar=None, area_acres=None, rainfall_normal_mm=None,
         source=None, irrigation_cost_per_ha=None, duration_days=None):
    """Water requirement, irrigation event count and indicative cost for one crop."""
    crop = (crop or "").strip().lower()
    water = water or {}
    calendar = calendar or calendar_for(crop) or {}
    req = requirement(crop, calendar)

    interval = _num(water.get("interval_days"), req["interval_days"])
    duration = _num(duration_days, _num(calendar.get("duration_days"), req["duration_days"])) or DEFAULT_DURATION_DAYS
    events = int(round(duration / interval)) if interval else None
    rate = _num(irrigation_cost_per_ha)
    if rate is None:
        rate = irrigation_rate()
    area = _num(area_acres) if area_acres else None

    cost_per_ha = round(events * rate) if events else None
    cost_per_acre = round(events * rate / ACRES_PER_HA) if events else None

    source_known = bool(water.get("known"))
    sufficient = water.get("sufficient") if source_known else None

    suggestions_en = []
    suggestions_mr = []
    if events:
        suggestions_en.append(
            f"Plan roughly {events} irrigations across the season (one every ~{interval:.0f} days)."
        )
        suggestions_mr.append(
            f"हंगामात अंदाजे {events} सिंचन ठरवा (दर ~{interval:.0f} दिवसांनी एक)."
        )
    if source_known and sufficient is False:
        suggestions_en.append(
            "Your declared supply is below the crop need - stretch the interval, mulch, or pick a lower-water crop."
        )
        suggestions_mr.append(
            "तुमचा नोंदवलेला पुरवठा पिकाच्या गरजेपेक्षा कमी आहे - अंतर वाढवा, मल्चिंग करा किंवा कमी पाण्याचे पीक निवडा."
        )
    if source_known and sufficient is True:
        suggestions_en.append("Your supply looks adequate for this crop - keep the root zone evenly moist.")
        suggestions_mr.append("तुमचा पुरवठा या पिकासाठी पुरेसा दिसतो - मुळांचा भाग समान ओला ठेवा.")
    if not events:
        suggestions_en.append("Irrigation interval data is missing for this crop, so no event count is shown.")
        suggestions_mr.append("या पिकासाठी सिंचन अंतराची माहिती नाही, त्यामुळे सिंचन संख्या दर्शवलेली नाही.")

    return {
        "available": bool(req["season_mm"] or events),
        "crop": crop,
        "season_mm": req["season_mm"],
        "need": water.get("need") or req["need"],
        "interval_days": round(interval) if interval else None,
        "duration_days": round(duration),
        "events": events,
        "source": source or water.get("source"),
        "source_known": source_known,
        "sufficient": sufficient,
        "rainfall_normal_mm": _num(rainfall_normal_mm),
        "area_acres": area,
        "cost": {
            "per_event_per_ha": rate,
            "per_ha": cost_per_ha,
            "per_acre": cost_per_acre,
            "total": round(cost_per_acre * area) if (cost_per_acre and area) else None,
        } if events else None,
        "suggestions_en": suggestions_en, "suggestions_mr": suggestions_mr,
        "note_en": "Season water need is indicative crop data; rainfall shown is the monthly normal, not a forecast.",
        "note_mr": "हंगामातील पाण्याची गरज सूचक पिक माहिती आहे; दर्शवलेला पाऊस महिन्याचा सरासरी आहे, अंदाज नाही.",
    }


def compare(crops, water_reports=None, area_acres=None):
    """Rows sorted by seasonal water need (lowest first) for a shortlist of crops."""
    water_reports = water_reports or {}
    rows = []
    for crop in crops:
        report = plan(crop, water=water_reports.get(crop), area_acres=area_acres)
        rows.append(report)
    rows.sort(key=lambda row: (not row.get("available"), row.get("season_mm") or 0))
    return rows
