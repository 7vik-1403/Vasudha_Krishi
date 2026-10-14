import json
import os

import requests

from utils.geo import DATA_DIR, cache_get, cache_set

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
DEFAULTS_PATH = os.path.join(DATA_DIR, "soil_defaults.json")

PROPERTIES = ["phh2o", "soc", "nitrogen", "clay", "sand", "silt"]
DEPTHS = ["0-5cm", "5-15cm", "15-30cm"]
DEPTH_WEIGHTS = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15}
D_FACTORS = {"phh2o": 10, "soc": 10, "nitrogen": 10, "clay": 10, "sand": 10, "silt": 10}

_TEXTURE_MR = {
    "Clay": "चिकण",
    "Silty clay": "गाळमय चिकण",
    "Sand": "वाळू",
    "Sandy loam": "वाळुमय दुमट",
    "Silt loam": "गाळमय दुमट",
    "Clay loam": "चिकण दुमट",
    "Loam": "दुमट",
}


def load_defaults():
    with open(DEFAULTS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _texture_class(clay, sand, silt):
    if clay is None or sand is None or silt is None:
        return None
    if clay >= 40 and silt < 40 and sand < 45:
        return "Clay"
    if silt >= 40 and clay >= 27:
        return "Silty clay"
    if sand >= 70:
        return "Sand"
    if sand >= 43 and clay < 20:
        return "Sandy loam"
    if silt >= 50 and clay < 27:
        return "Silt loam"
    if 27 <= clay < 40 and 20 <= sand < 45:
        return "Clay loam"
    if clay < 27 and silt >= 28 and sand <= 52:
        return "Loam"
    return "Loam"


def _soilgrids(lat, lon):
    key = f"soilgrids|{round(float(lat), 4)}|{round(float(lon), 4)}"
    cached = cache_get(key, max_age_hours=24 * 60)
    if cached is not None:
        return cached
    params = [
        ("lon", round(float(lon), 5)),
        ("lat", round(float(lat), 5)),
        ("value", "mean"),
    ]
    for depth_label in DEPTHS:
        params.append(("depth", depth_label))
    for prop in PROPERTIES:
        params.append(("property", prop))
    try:
        resp = requests.get(SOILGRIDS_URL, params=params, timeout=25)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError):
        return None

    weights = [DEPTH_WEIGHTS[d] for d in DEPTHS]
    total_weight = sum(weights)
    result = {}
    for layer in payload.get("properties", {}).get("layers", []):
        name = layer.get("name")
        factor = D_FACTORS.get(name, 1)
        by_label = {
            (depth.get("label") or depth.get("range", {}).get("top_depth")): (depth.get("values") or {}).get("mean")
            for depth in layer.get("depths", [])
        }
        picked, weight_sum = 0.0, 0
        for depth_label, weight in zip(DEPTHS, weights):
            mean = by_label.get(depth_label)
            if mean is not None:
                picked += (mean / factor) * weight
                weight_sum += weight
        if weight_sum == 0:
            result[name] = None
        elif weight_sum == total_weight:
            result[name] = round(picked / total_weight, 3)
        else:
            result[name] = round(picked / weight_sum, 3)
    if not any(v is not None for v in result.values()):
        result = {"_nodata": True}
    cache_set(key, result)
    return result


def soil_at_point(lat, lon, language="en"):
    mr = language == "mr"
    defaults = load_defaults()
    grid = _soilgrids(lat, lon) or {}
    warnings = []
    ph = grid.get("phh2o")
    if ph is None:
        warnings.append(
            "या ठिकाणासाठी SoilGrids pH उपलब्ध नाही; नमुना डीफॉल्ट pH वापरला."
            if mr
            else "SoilGrids pH is not available for this point; using demo default pH."
        )
        ph = round(defaults["ph"], 2)

    clay = grid.get("clay")
    sand = grid.get("sand")
    silt = grid.get("silt")
    soc = grid.get("soc")
    total_n = grid.get("nitrogen")

    texture = _texture_class(clay, sand, silt)
    if mr and texture:
        texture = _TEXTURE_MR.get(texture, texture)

    result = {
        "ph": ph,
        "ph_source": (
            "SoilGrids २५० मी (ISRIC) - जागतिक माती नकाशा"
            if grid.get("phh2o") is not None
            else "नमुना डीफॉल्ट (डेटासेट मध्यक)"
        ) if mr else (
            "SoilGrids 250 m (ISRIC) - modelled global soil map"
            if grid.get("phh2o") is not None
            else "demo default (dataset median)"
        ),
        "organic_carbon_g_kg": soc,
        "total_nitrogen_g_kg": total_n,
        "clay_pct": clay,
        "sand_pct": sand,
        "silt_pct": silt,
        "texture_class": texture,
        "available": {
            "n": float(defaults["N"]),
            "p": float(defaults["P"]),
            "k": float(defaults["K"]),
            "unit": "kg/ha",
            "source": (
                "Crop Recommendation डेटासेट मध्यकावर आधारित नमुना डीफॉल्ट - मोजलेले नाही. अचूक सल्ल्यासाठी तुमची Soil Health Card N/P/K टाका."
                if mr
                else "Demo defaults from the Crop Recommendation dataset medians - NOT measured. Enter your Soil Health Card N/P/K for accurate advice."
            ),
            "measured": False,
        },
        "sources": {
            "grid": "SoilGrids 250 m (ISRIC, rest.isric.org)" if grid else None,
            "available_npk": "dataset median demo default",
        },
        "warnings": warnings,
    }
    if not grid:
        warnings.append(
            "SoilGrids उपलब्ध नाही; फक्त नमुना डीफॉल्ट दाखवले आहेत."
            if mr
            else "SoilGrids was unreachable; only demo defaults are shown."
        )
    elif grid.get("_nodata"):
        warnings.append(
            "या ठिकाणासाठी SoilGrids मध्ये माहिती नाही; फक्त नमुना डीफॉल्ट दाखवले आहेत."
            if mr
            else "SoilGrids has no data coverage at this point; only demo defaults are shown."
        )
    return result


def apply_farmer_soil(soil, n=None, p=None, k=None, ph=None, language="en"):
    """Overlay farmer-entered Soil Health Card values onto a resolver soil dict."""
    mr = language == "mr"
    soil = dict(soil)
    available = dict(soil.get("available") or {})
    entered = []
    if n is not None:
        available["n"] = float(n)
        entered.append("N")
    if p is not None:
        available["p"] = float(p)
        entered.append("P")
    if k is not None:
        available["k"] = float(k)
        entered.append("K")
    if entered:
        available["source"] = (
            "शेतकऱ्याने टाकलेली Soil Health Card मूल्ये"
            if mr
            else "Farmer-entered Soil Health Card values"
        )
        available["measured"] = True
    soil["available"] = available
    if ph is not None:
        soil["ph"] = float(ph)
        soil["ph_source"] = (
            "शेतकऱ्याने टाकलेले (Soil Health Card / माती चाचणी)"
            if mr
            else "Farmer-entered (Soil Health Card / soil test)"
        )
    return soil
