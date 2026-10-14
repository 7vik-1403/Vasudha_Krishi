"""Market price context for a crop: notified MSP, last-year comparison and revenue estimate."""

import csv
import os

from cropapp.services.datasets import DATA_DIR, calendar_for, load_requirements, msp_for

ACRES_PER_HA = 2.47105
_HISTORY = None

HISTORY_SOURCE = "CACP notified MSP (Agriculture Marketing Division, Govt. of India)"
VERIFY_NOTE = "Verify today's mandi rate on e-NAM or Agmarknet before selling."
VERIFY_NOTE_MR = "विक्रीपूर्वी आजचा मंडी दर e-NAM किंवा Agmarknet वर तपासा."


def _history():
    global _HISTORY
    if _HISTORY is None:
        _HISTORY = {}
        path = os.path.join(DATA_DIR, "market_history.csv")
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    price = float(row["msp"])
                except (KeyError, TypeError, ValueError):
                    continue
                _HISTORY.setdefault(row["crop"].strip().lower(), {})[row["year"].strip()] = price
    return _HISTORY


def history_for(crop):
    """{year: msp} for one crop, oldest first as a list of (year, price)."""
    rows = _history().get((crop or "").strip().lower(), {})
    return sorted(rows.items())


def market_for(crop, area_acres=None):
    crop = (crop or "").strip().lower()
    msp = msp_for(crop)
    history = history_for(crop)

    current = msp.get("price")
    current_year = msp.get("year")
    previous = previous_year = None
    years = [year for year, _ in history]
    if current_year in years:
        index = years.index(current_year)
        if index > 0:
            previous_year, previous = history[index - 1]
    elif len(history) >= 2:
        previous_year, previous = history[-2]
        current_year, current = history[-1]

    change_pct = None
    if current and previous:
        change_pct = round((current - previous) / previous * 100.0, 1)

    trend = None
    if change_pct is not None:
        trend = "up" if change_pct > 0 else ("down" if change_pct < 0 else "flat")

    revenue = _revenue(crop, current, area_acres)
    calendar = calendar_for(crop) or {}

    if current:
        advice_en = (
            f"Notified MSP for {current_year} is Rs {int(current)} per quintal"
            + (f", {abs(change_pct)}% {'higher' if change_pct > 0 else 'lower'} than {previous_year}" if change_pct is not None else "")
            + f". Rough gross value at the target yield: Rs {revenue['per_acre']:,}/acre."
        )
        advice_mr = (
            f"{current_year} साठी जाहीर केलेली किम्मी रु {int(current)} प्रति क्विंटल"
            + (f", {previous_year} पेक्षा {abs(change_pct)}% {'जास्त' if change_pct > 0 else 'कमी'}" if change_pct is not None else "")
            + f". अंदाजे उत्पादनेनुसार दर: रु {revenue['per_acre']:,}/एकर."
        )
    else:
        advice_en = msp.get("note") or "No notified MSP for this crop - it trades at market (mandi) rates."
        advice_mr = "या पिकासाठी जाहीर किम्मी नाही - याची मंडी किम्मी लागू आहे."

    return {
        "crop": crop,
        "price": current,
        "unit": msp.get("unit"),
        "year": current_year,
        "prev_price": previous,
        "prev_year": previous_year,
        "change_pct": change_pct,
        "trend": trend,
        "history": [{"year": year, "price": price} for year, price in history],
        "revenue": revenue,
        "source": msp.get("source") or HISTORY_SOURCE,
        "note": msp.get("note"),
        "verify": VERIFY_NOTE,
        "verify_mr": VERIFY_NOTE_MR,
        "advice_en": advice_en,
        "advice_mr": advice_mr,
        "has_history": bool(history),
        "harvest_days": calendar.get("duration_days"),
    }


def _revenue(crop, price, area_acres):
    requirement = load_requirements().get(crop) or {}
    yield_t_ha = requirement.get("target_yield_t_ha")
    if not price or not yield_t_ha:
        return None
    per_ha = price * yield_t_ha * 100  # quintal = 100 kg, yield is t/ha
    per_acre = round(per_ha / ACRES_PER_HA)
    return {
        "yield_t_ha": yield_t_ha,
        "per_acre": per_acre,
        "per_hectare": round(per_ha),
        "total": round(per_acre * area_acres) if area_acres else None,
        "area_acres": area_acres,
    }


def compare_markets(crops, area_acres=None):
    """Market row for each candidate so the farmer can pick on price as well as fit."""
    rows = [market_for(crop, area_acres) for crop in crops]
    rows.sort(key=lambda row: (row["price"] is None, -(row["price"] or 0)))
    return rows
