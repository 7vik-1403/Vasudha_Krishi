from cropapp.services.datasets import calendar_for, load_fertilizer_prices, load_requirements
from cropapp.services.units import ACRES_PER_HA

# Nutrient content of the standard Indian fertilizer grades.
PRODUCTS = {
    "urea": {"label": "Urea (46-0-0)", "n": 0.46, "p2o5": 0.0, "k2o": 0.0},
    "dap": {"label": "DAP (18-46-0)", "n": 0.18, "p2o5": 0.46, "k2o": 0.0},
    "mop": {"label": "MOP / Muriate of potash (0-0-60)", "n": 0.0, "p2o5": 0.0, "k2o": 0.60},
}

# Agronomic recovery of applied nutrient (Indian extension defaults).
EFFICIENCY = {"n": 0.60, "p": 0.70, "k": 0.80}

# Elemental nutrient -> oxide form used on the fertilizer bag.
P_TO_P2O5 = 2.29
K_TO_K2O = 1.20


def _round(value):
    return round(value, 1)


def compute_fertilizer(crop, soil_npk, area_acres=None, npk_measured=True):
    """Deficit-based dose: (crop need - soil supply) / recovery efficiency, expressed as urea/DAP/MOP."""
    requirements = load_requirements().get((crop or "").strip().lower())
    if not requirements:
        return {
            "available": False,
            "note": f"No nutrient requirement data for '{crop}'.",
            "note_mr": f"'{crop}' साठी पोषक घटक गरजेची माहिती नाही.",
        }

    available = {
        "n": float(soil_npk.get("n") or 0.0),
        "p": float(soil_npk.get("p") or 0.0),
        "k": float(soil_npk.get("k") or 0.0),
    }
    deficits = {
        key: max(0.0, requirements[key] - available.get(key, 0.0)) for key in ("n", "p", "k")
    }
    to_apply = {
        "n": deficits["n"] / EFFICIENCY["n"],
        "p": deficits["p"] / EFFICIENCY["p"],
        "k": deficits["k"] / EFFICIENCY["k"],
    }
    products = {
        "urea_kg_ha": _round(to_apply["n"] / PRODUCTS["urea"]["n"]),
        "dap_kg_ha": _round((to_apply["p"] * P_TO_P2O5) / PRODUCTS["dap"]["p2o5"]),
        "mop_kg_ha": _round((to_apply["k"] * K_TO_K2O) / PRODUCTS["mop"]["k2o"]),
    }

    supplied_n = products["urea_kg_ha"] * PRODUCTS["urea"]["n"] + products["dap_kg_ha"] * PRODUCTS["dap"]["n"]
    supplied_p = products["dap_kg_ha"] * PRODUCTS["dap"]["p2o5"] / P_TO_P2O5
    supplied_k = products["mop_kg_ha"] * PRODUCTS["mop"]["k2o"] / K_TO_K2O
    ratio = _npk_ratio(supplied_n, supplied_p, supplied_k)

    per_acre = {
        "urea": _round(products["urea_kg_ha"] / ACRES_PER_HA),
        "dap": _round(products["dap_kg_ha"] / ACRES_PER_HA),
        "mop": _round(products["mop_kg_ha"] / ACRES_PER_HA),
    }
    plan = _stage_plan(crop, products)

    notes = []
    notes_mr = []
    if not npk_measured:
        notes.append(
            "N/P/K came from demo default values (not a soil test). Enter Soil Health Card values for a dependable dose."
        )
        notes_mr.append(
            "N/P/K ही नमुना डीफॉल्ट मूल्ये आहेत (माती चाचणी नाही). विश्वासार्ह डोससाठी Soil Health Card मूल्ये टाका."
        )
    if all(value == 0 for value in products.values()):
        notes.append("Soil already meets the crop's N, P and K need at this target yield - no dose required.")
        notes_mr.append("या लक्ष्य उत्पादनासाठी मातीत पिकाची N, P व K गरज आधीच पूर्ण आहे - डोस नको.")
    elif deficits["n"] == 0:
        notes.append("Soil nitrogen is sufficient; only phosphorus and potash need topping up.")
        notes_mr.append("मातीतील नायट्रोजन पुरेसा आहे; फक्त फॉस्फरस व पोटॅश भरावा लागेल.")
    if requirements.get("note"):
        notes.append(requirements["note"])

    result = {
        "available": True,
        "target_yield_t_ha": requirements["target_yield_t_ha"],
        "requirement_kg_ha": {"n": requirements["n"], "p": requirements["p"], "k": requirements["k"]},
        "soil_available_kg_ha": {key: _round(value) for key, value in available.items()},
        "deficit_kg_ha": {key: _round(value) for key, value in deficits.items()},
        "products": products,
        "per_acre": per_acre,
        "npk_ratio": ratio,
        "plan": plan,
        "npk_measured": bool(npk_measured),
        "notes": notes,
        "notes_mr": notes_mr,
        "disclaimer": (
            "Indicative dose for the yield target in the reference table. Split applications as per the plan; "
            "calibrate with a local soil test and KVK advice."
        ),
        "disclaimer_mr": (
            "संदर्भ तक्त्यातील उत्पादन लक्ष्यासाठी हा सूचक डोस आहे. आराखड्यानुसार विभागून द्या; "
            "स्थानिक माती चाचणी व KVK सल्ल्यानुसार त्यात बदल करा."
        ),
    }
    result["cost"] = _cost_options(products, area_acres=area_acres)
    if area_acres:
        area = float(area_acres)
        result["area_acres"] = area
        result["total_for_farm"] = {
            key: _round(value * area) for key, value in per_acre.items()
        }
    return result


VEGETABLE_SPLITS = (
    {"day": 0, "en": "Basal dose", "mr": "आधार खत", "n": 0.34, "p": 1.0, "k": 0.5},
    {"day": 25, "en": "First top dress", "mr": "पहिली परवडी", "n": 0.33, "p": 0.0, "k": 0.3},
    {"day": 50, "en": "Second top dress", "mr": "दुसरी परवडी", "n": 0.33, "p": 0.0, "k": 0.2},
)


def compute_vegetable_fertilizer(veg, area_acres=None):
    """Convert the vegetable's indicative N / P2O5 / K2O rates into urea, DAP and MOP."""
    npk = (veg or {}).get("npk_kg_ha") or {}
    rates = {"n": float(npk.get("n") or 0), "p": float(npk.get("p") or 0), "k": float(npk.get("k") or 0)}
    products = {
        "urea_kg_ha": _round(rates["n"] / PRODUCTS["urea"]["n"]),
        "dap_kg_ha": _round(rates["p"] / PRODUCTS["dap"]["p2o5"]),
        "mop_kg_ha": _round(rates["k"] / PRODUCTS["mop"]["k2o"]),
    }
    plan = []
    for split in VEGETABLE_SPLITS:
        stage = {
            "day": split["day"],
            "stage_en": split["en"],
            "stage_mr": split["mr"],
            "urea_kg_ha": _round(products["urea_kg_ha"] * split["n"]),
            "dap_kg_ha": _round(products["dap_kg_ha"] * split["p"]),
            "mop_kg_ha": _round(products["mop_kg_ha"] * split["k"]),
            "n_share": split["n"],
            "p_share": split["p"],
            "k_share": split["k"],
        }
        stage["per_acre"] = {key: _round(stage[f"{key}_kg_ha"] / ACRES_PER_HA) for key in ("urea", "dap", "mop")}
        plan.append(stage)

    result = {
        "available": True,
        "vegetable": True,
        "target_yield_t_ha": None,
        "requirement_kg_ha": {"n": rates["n"], "p": rates["p"], "k": rates["k"]},
        "requirement_note": "N in kg N/ha; P and K are kg P2O5 and kg K2O per hectare (indicative extension rates).",
        "requirement_note_mr": "N हे किग्रॅ N/हेक्टर; P व K ही किग्रॅ P2O5 व किग्रॅ K2O प्रति हेक्टर (सूचक विस्तार दर).",
        "soil_available_kg_ha": {"n": 0.0, "p": 0.0, "k": 0.0},
        "deficit_kg_ha": {"n": rates["n"], "p": rates["p"], "k": rates["k"]},
        "products": products,
        "per_acre": {key: _round(value / ACRES_PER_HA) for key, value in (
            ("urea", products["urea_kg_ha"]),
            ("dap", products["dap_kg_ha"]),
            ("mop", products["mop_kg_ha"]),
        )},
        "npk_ratio": _npk_ratio(rates["n"], rates["p"], rates["k"]),
        "plan": plan,
        "npk_measured": False,
        "notes": ["Vegetable rates are indicative - split them as per the plan and confirm with your local KVK."],
        "notes_mr": ["भाजीपाल्याचे दर सूचक आहेत - आराखड्यानुसार विभागून द्या व स्थानिक KVK कडून खात्री करा."],
        "disclaimer": "Indicative vegetable dose; adjust to soil test, season and local advisory.",
        "disclaimer_mr": "सूचक भाजीपाला डोस; माती चाचणी, हंगाम व स्थानिक सल्ल्यानुसार बदल करा.",
    }
    result["cost"] = _cost_options(products, area_acres=area_acres)
    if area_acres:
        area = float(area_acres)
        result["area_acres"] = area
        result["total_for_farm"] = {key: _round(value * area) for key, value in result["per_acre"].items()}
    return result


def _price(key):
    return float((load_fertilizer_prices().get(key) or {}).get("price_per_kg") or 0)


def _cost_options(products, area_acres=None):
    """Price the standard Urea+DAP+MOP plan and a cheaper Urea+SSP+MOP plan that delivers
    the same N, P2O5 and K2O. Returns per-hectare, per-acre and farm totals plus savings."""
    urea = float(products.get("urea_kg_ha") or 0)
    dap = float(products.get("dap_kg_ha") or 0)
    mop = float(products.get("mop_kg_ha") or 0)

    p_urea = float(PRODUCTS["urea"]["n"])
    p_dap_p = float(PRODUCTS["dap"]["p2o5"])
    p_mop_k = float(PRODUCTS["mop"]["k2o"])
    ssp_p = float((load_fertilizer_prices().get("ssp") or {}).get("p2o5") or 0.16)

    # Nutrient actually delivered by the standard plan (identical for the economy plan).
    n_delivered = urea * p_urea + dap * PRODUCTS["dap"]["n"]
    p2o5_delivered = dap * p_dap_p
    k2o_delivered = mop * p_mop_k

    econ_urea = (n_delivered / p_urea) if p_urea else 0.0
    econ_ssp = (p2o5_delivered / ssp_p) if ssp_p else 0.0
    econ_mop = (k2o_delivered / p_mop_k) if p_mop_k else 0.0

    if area_acres:
        area = float(area_acres)
    else:
        area = None

    def option(key, label_en, label_mr, kg):
        lines = []
        cost_ha = 0.0
        for product_key, amount in kg.items():
            if not amount:
                continue
            unit_price = _price(product_key)
            line_cost = amount * unit_price
            cost_ha += line_cost
            meta = load_fertilizer_prices().get(product_key) or {}
            lines.append(
                {
                    "key": product_key,
                    "label_en": meta.get("label_en") or product_key.upper(),
                    "label_mr": meta.get("label_mr") or product_key.upper(),
                    "kg_ha": _round(amount),
                    "kg_acre": _round(amount / ACRES_PER_HA),
                    "price_per_kg": _round(unit_price),
                    "cost_ha": round(line_cost),
                }
            )
        cost_acre = cost_ha / ACRES_PER_HA
        return {
            "key": key,
            "label_en": label_en,
            "label_mr": label_mr,
            "products": {product_key: _round(amount) for product_key, amount in kg.items() if amount},
            "lines": lines,
            "cost_per_ha": round(cost_ha),
            "cost_per_acre": round(cost_acre),
            "total": round(cost_acre * area) if area else None,
        }

    standard = option(
        "standard", "Standard (Urea + DAP + MOP)", "प्रमाणित (युरिया + डीएपी + एमओपी)",
        {"urea": urea, "dap": dap, "mop": mop},
    )
    economy = option(
        "economy", "Economy (Urea + SSP + MOP)", "किफायतशीर (युरिया + एसएसपी + एमओपी)",
        {"urea": econ_urea, "ssp": econ_ssp, "mop": econ_mop},
    )

    cheapest = min(standard, economy, key=lambda item: item["cost_per_ha"])
    for item in (standard, economy):
        item["is_cheapest"] = item is cheapest

    savings_acre = standard["cost_per_acre"] - economy["cost_per_acre"]
    savings = {
        "per_acre": round(savings_acre),
        "per_ha": round(standard["cost_per_ha"] - economy["cost_per_ha"]),
        "total": round(savings_acre * area) if area else None,
        "pct": round(savings_acre / standard["cost_per_acre"] * 100) if standard["cost_per_acre"] else 0,
    }

    return {
        "options": [standard, economy],
        "standard": standard,
        "economy": economy,
        "savings": savings,
        "area_acres": area,
        "price_source": "Indicative retail MRP (editable in data/fertilizer_prices.csv)",
        "price_source_mr": "सूचक किरकोळ एमआरपी (data/fertilizer_prices.csv मध्ये बदलता येते)",
        "disclaimer": "Fertilizer prices are indicative national MRP - confirm your local dealer rate; SSP is bulkier than DAP.",
        "disclaimer_mr": "खतांचे दर सूचक राष्ट्रीय एमआरपी आहेत - स्थानिक विक्रेत्याचा दर तपासा; एसएसपी डीएपीपेक्षा जास्त वजनाचा असतो.",
    }


def _npk_ratio(n, p, k):
    values = [n, p, k]
    peak = max(values)
    if peak <= 0:
        return "0:0:0"
    scale = 100.0 / peak
    parts = [int(round(v * scale)) for v in values]
    parts = [max(1, part) if value > 0 else 0 for part, value in zip(parts, values)]
    return ":".join(str(part) for part in parts)


def _stage_plan(crop, products):
    calendar = calendar_for(crop)
    splits = (calendar or {}).get("fertilizer") or []
    plan = []
    for split in splits:
        n_share = float(split.get("n") or 0.0)
        p_share = float(split.get("p") or 0.0)
        k_share = float(split.get("k") or 0.0)
        if not (n_share or p_share or k_share):
            continue
        stage = {
            "day": split.get("day"),
            "stage_en": split.get("en"),
            "stage_mr": split.get("mr"),
            "urea_kg_ha": _round(products["urea_kg_ha"] * n_share),
            "dap_kg_ha": _round(products["dap_kg_ha"] * p_share),
            "mop_kg_ha": _round(products["mop_kg_ha"] * k_share),
            "n_share": n_share,
            "p_share": p_share,
            "k_share": k_share,
        }
        stage["per_acre"] = {
            "urea": _round(stage["urea_kg_ha"] / ACRES_PER_HA),
            "dap": _round(stage["dap_kg_ha"] / ACRES_PER_HA),
            "mop": _round(stage["mop_kg_ha"] / ACRES_PER_HA),
        }
        plan.append(stage)
    return plan
