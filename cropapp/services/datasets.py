import csv
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")

_MSP = None
_REQUIREMENTS = None
_CALENDAR = None
_CATEGORIES = None
_WATER = None
_VEGETABLES = None
_SUITABILITY = None
_FERTILIZER_PRICES = None
_SPRAY_PRODUCTS = None
_CROP_SPRAYS = None
_ECONOMICS = None


def _read_json(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def _msp_rows():
    global _MSP
    if _MSP is None:
        with open(os.path.join(DATA_DIR, "msp.csv"), newline="", encoding="utf-8") as fh:
            _MSP = {row["crop"].strip(): row for row in csv.DictReader(fh)}
    return _MSP


def msp_for(crop):
    row = _msp_rows().get((crop or "").strip().lower())
    if not row:
        return {"crop": crop, "price": None, "unit": None, "source": None, "note": "No MSP entry for this crop."}
    price = None
    if row.get("msp"):
        try:
            price = float(row["msp"])
        except ValueError:
            price = None
    return {
        "crop": row["crop"],
        "price": price,
        "unit": row.get("unit"),
        "year": row.get("year"),
        "source": row.get("source"),
        "note": row.get("note"),
    }


def load_requirements():
    global _REQUIREMENTS
    if _REQUIREMENTS is None:
        _REQUIREMENTS = {}
        with open(os.path.join(DATA_DIR, "crop_npk_requirements.csv"), newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    _REQUIREMENTS[row["crop"].strip()] = {
                        "target_yield_t_ha": float(row["target_yield_t_ha"]),
                        "n": float(row["n_kg_ha"]),
                        "p": float(row["p_kg_ha"]),
                        "k": float(row["k_kg_ha"]),
                        "note": row.get("note"),
                    }
                except (KeyError, ValueError):
                    continue
    return _REQUIREMENTS


def load_calendar():
    global _CALENDAR
    if _CALENDAR is None:
        _CALENDAR = _read_json("crop_calendar.json")
    return _CALENDAR


def calendar_for(crop):
    return load_calendar().get((crop or "").strip().lower())


def load_categories():
    """{'cereals': {'label_en', 'label_mr', 'icon', 'crops': [...]}, ...} without the _meta key."""
    global _CATEGORIES
    if _CATEGORIES is None:
        raw = _read_json("crop_categories.json")
        _CATEGORIES = {key: value for key, value in raw.items() if not key.startswith("_")}
    return _CATEGORIES


def category_of(crop):
    crop = (crop or "").strip().lower()
    for key, value in load_categories().items():
        if crop in value.get("crops", []):
            return key
    return None


def crops_in_category(category):
    return list((load_categories().get(category) or {}).get("crops", []))


def load_water():
    global _WATER
    if _WATER is None:
        raw = _read_json("crop_water.json")
        _WATER = {key: value for key, value in raw.items() if not key.startswith("_")}
    return _WATER


def water_for(crop):
    return load_water().get((crop or "").strip().lower())


def load_vegetables():
    global _VEGETABLES
    if _VEGETABLES is None:
        raw = _read_json("vegetables.json")
        _VEGETABLES = {key: value for key, value in raw.items() if not key.startswith("_")}
    return _VEGETABLES


def load_suitability():
    global _SUITABILITY
    if _SUITABILITY is None:
        raw = _read_json("crop_suitability.json")
        _SUITABILITY = {key: value for key, value in raw.items() if not key.startswith("_")}
    return _SUITABILITY


def suitability_for(crop):
    return load_suitability().get((crop or "").strip().lower())


def load_fertilizer_prices():
    global _FERTILIZER_PRICES
    if _FERTILIZER_PRICES is None:
        _FERTILIZER_PRICES = {}
        with open(os.path.join(DATA_DIR, "fertilizer_prices.csv"), newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                key = (row.get("key") or "").strip().lower()
                if not key:
                    continue
                try:
                    _FERTILIZER_PRICES[key] = {
                        "label_en": row.get("name_en"),
                        "label_mr": row.get("name_mr"),
                        "n": float(row.get("n") or 0),
                        "p2o5": float(row.get("p2o5") or 0),
                        "k2o": float(row.get("k2o") or 0),
                        "price_per_kg": float(row.get("price_per_kg") or 0),
                        "bag_kg": float(row.get("bag_kg") or 0) or None,
                        "source": row.get("source"),
                        "note": row.get("note"),
                    }
                except (TypeError, ValueError):
                    continue
    return _FERTILIZER_PRICES


def load_spray_products():
    global _SPRAY_PRODUCTS
    if _SPRAY_PRODUCTS is None:
        raw = _read_json("spray_products.json")
        _SPRAY_PRODUCTS = {key: value for key, value in raw.items() if not key.startswith("_")}
    return _SPRAY_PRODUCTS


def load_crop_sprays():
    global _CROP_SPRAYS
    if _CROP_SPRAYS is None:
        raw = _read_json("crop_sprays.json")
        _CROP_SPRAYS = {key: value for key, value in raw.items() if not key.startswith("_")}
    return _CROP_SPRAYS


def load_economics():
    """Indicative input-cost assumptions for the profitability calculator (never market data)."""
    global _ECONOMICS
    if _ECONOMICS is None:
        raw = _read_json("crop_economics.json")
        _ECONOMICS = {
            "meta": raw.get("_meta") or {},
            "defaults": raw.get("defaults") or {},
            "scenarios": raw.get("scenarios") or {},
            "crops": raw.get("crops") or {},
        }
    return _ECONOMICS

