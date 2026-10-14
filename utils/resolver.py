from datetime import date

from utils.climate import climate_for_point
from utils.geo import district_center, district_for_point, reverse_geocode
from utils.soil import load_defaults, soil_at_point


def _apply_climate_fallbacks(climate):
    """Fill any climate feature the network could not provide so the model always gets 7 features."""
    defaults = load_defaults()
    missing = []
    if climate.get("temp_c") is None:
        climate["temp_c"] = round(defaults["temperature"], 1)
        missing.append("temperature")
    if climate.get("humidity_pct") is None:
        climate["humidity_pct"] = round(defaults["humidity"], 1)
        missing.append("humidity")
    rainfall = climate.get("rainfall_normal_mm") or climate.get("rainfall_actual_mm")
    if rainfall is None:
        rainfall = round(defaults["rainfall"], 1)
        missing.append("rainfall")
    climate["rainfall_model_mm"] = round(float(rainfall), 1)
    climate["fallbacks"] = missing
    return climate


_FALLBACK_MR = {"temperature": "तापमान", "humidity": "आर्द्रता", "rainfall": "पाऊस"}


def resolve(lat, lon, month=None, reverse=True, language="en"):
    """Resolve a map pin into district + soil + climate with source provenance for every field."""
    mr = language == "mr"
    lat = float(lat)
    lon = float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("अक्षांश/रेखांश मर्यादेबाहेर" if mr else "latitude/longitude out of range")
    if not (6.0 <= lat <= 37.5 and 68.0 <= lon <= 97.5):
        raise ValueError(
            "हे स्थान भारताबाहेर आहे; ही प्रणाली भारतीय कृषी-हवामान डेटा वापरते."
            if mr
            else "location is outside India; this system uses Indian agro-climate datasets"
        )

    month = int(month) if month else date.today().month
    if not 1 <= month <= 12:
        raise ValueError("महिना १ ते १२ दरम्यान असावा" if mr else "month must be between 1 and 12")

    geo = district_for_point(lat, lon) or {}
    place = (reverse_geocode(lat, lon) or {}) if reverse else {}
    district = geo.get("district") or place.get("district")
    state = geo.get("state") or place.get("state")

    climate = _apply_climate_fallbacks(
        climate_for_point(lat, lon, month=month, district=district, state=state)
    )
    soil = soil_at_point(lat, lon, language=language)

    warnings = list(soil.get("warnings") or [])
    if climate.get("fallbacks"):
        if mr:
            missing = ", ".join(_FALLBACK_MR.get(key, key) for key in climate["fallbacks"])
            warnings.append("थेट हवामान उपलब्ध नव्हते; यासाठी डीफॉल्ट मूल्ये वापरली: " + missing + ".")
        else:
            warnings.append(
                "Live weather was unavailable; default values were used for: " + ", ".join(climate["fallbacks"]) + "."
            )
    if not geo:
        warnings.append(
            "जिल्हा बहुभुज जुळला नाही; पावसाची सरासरी उपलब्ध नसू शकते. समन्वय हवामानासाठी वापरता येतात."
            if mr
            else "District polygon not matched; rainfall normal may be unavailable. Coordinates still work for climate."
        )
    if climate.get("rainfall_normal_mm") is None and district:
        warnings.append(
            f'जिल्हा "{district}" साठी IMD पावसाची सरासरी जुळली नाही.'
            if mr
            else f'IMD rainfall normal not matched for district "{district}".'
        )

    return {
        "lat": lat,
        "lon": lon,
        "month": month,
        "location": {
            "district": district,
            "state": state,
            "dt_code": geo.get("dt_code"),
            "village": place.get("village"),
            "pincode": place.get("pincode"),
            "display_name": place.get("display_name"),
            "resolution": "map_pin",
            "sources": {
                "district_polygon": geo.get("source"),
                "place_name": place.get("source"),
            },
        },
        "soil": soil,
        "climate": climate,
        "warnings": warnings,
    }


def resolve_district(district, state=None, month=None, language="en"):
    """Same as resolve(), but starts from a district name (legacy state/district form)."""
    mr = language == "mr"
    lat, lon, feat = district_center(district, state)
    if feat is None:
        raise ValueError(
            f'जिल्हा "{district}" बंडल केलेल्या जिल्हा यादीत सापडला नाही'
            if mr
            else f'district "{district}" was not found in the bundled district list'
        )
    result = resolve(lat, lon, month=month, reverse=False, language=language)
    result["location"].update(
        {
            "district": feat["district"],
            "state": feat["state"],
            "dt_code": feat["dt_code"],
            "village": None,
            "pincode": None,
            "display_name": None,
            "resolution": "district_centroid",
        }
    )
    result["warnings"] = [
        w
        for w in result["warnings"]
        if "District polygon not matched" not in w and "जिल्हा बहुभुज जुळला नाही" not in w
    ]
    return result
