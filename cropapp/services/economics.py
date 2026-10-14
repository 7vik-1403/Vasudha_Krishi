"""Indicative crop profitability.

Input costs (seed, labour, other) come from editable planning assumptions in
data/crop_economics.json; fertilizer and spray costs come from the existing dose
and spray planners; the market price is the notified MSP and the target yield is
the reference figure for the crop. Nothing here is a market quote or a promise -
every figure is a labelled estimate, and best/expected/worst scenarios only scale
yield and price by transparent factors.
"""

from cropapp.services.datasets import load_economics, load_requirements
from cropapp.services.units import ACRES_PER_HA

SCENARIO_ORDER = ("worst", "expected", "best")

COST_LABELS = {
    "seed": ("Seed & planting material", "बियाणे व लागवड साहित्य"),
    "fertilizer": ("Fertilizer", "खत"),
    "spray": ("Plant protection / sprays", "फवारणी / कीड नियंत्रण"),
    "irrigation": ("Irrigation", "सिंचन"),
    "labor": ("Labour", "मजुरी"),
    "other": ("Other (machinery, misc.)", "इतर (यंत्रणा, इतर)"),
}


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def scenarios():
    return load_economics().get("scenarios") or {}


def scenario_names():
    names = list(scenarios().keys())
    return [name for name in SCENARIO_ORDER if name in names] + [name for name in names if name not in SCENARIO_ORDER]


def scenario_factors(name):
    data = scenarios().get(name) or scenarios().get("expected") or {}
    return {
        "yield_factor": _num(data.get("yield_factor"), 1.0),
        "price_factor": _num(data.get("price_factor"), 1.0),
        "label_en": data.get("label_en") or name,
        "label_mr": data.get("label_mr") or name,
    }


def crop_costs(crop):
    econ = load_economics()
    default = econ.get("defaults") or {}
    row = (econ.get("crops") or {}).get((crop or "").strip().lower()) or {}
    return {
        "seed": _num(row.get("seed_per_ha", default.get("seed_per_ha"))),
        "labor": _num(row.get("labor_per_ha", default.get("labor_per_ha"))),
        "other": _num(row.get("other_per_ha", default.get("other_per_ha"))),
    }


def irrigation_rate():
    return _num((load_economics().get("defaults") or {}).get("irrigation_event_cost_per_ha"))


def _assumptions(factors):
    return (
        [
            "Input costs (seed, labour, other) are editable planning assumptions in "
            "data/crop_economics.json, not market prices.",
            "Market price is the notified MSP - your mandi / contract rate can differ.",
            "Target yield is a reference figure for the crop, not a guarantee.",
            f"{factors['label_en']} scales yield x{factors['yield_factor']} and price x{factors['price_factor']}.",
        ],
        [
            "इनपुट खर्च (बियाणे, मजुरी, इतर) ही data/crop_economics.json मधील संपादन करण्यायोग्य गृहीतके आहेत, बाजारभाव नाहीत.",
            "बाजारभाव हा जाहीर किम्मी (MSP) आहे - तुमचा मंडी / करार दर वेगळा असू शकतो.",
            "लक्ष्य उत्पादन हा पिकासाठीचा संदर्भ आकडा आहे, हमी नाही.",
            f"{factors['label_mr']}: उत्पादन x{factors['yield_factor']}, दर x{factors['price_factor']}.",
        ],
    )


def profitability(crop, area_acres=None, market=None, fertilizer=None, spray=None,
                  irrigation_events=None, irrigation_cost_per_ha=None,
                  scenario="expected", overrides=None):
    """Estimate revenue, itemised costs and net profit for one crop (per ha / per acre / total)."""
    crop = (crop or "").strip().lower()
    market = market or {}
    overrides = overrides or {}
    factors = scenario_factors(scenario)

    price = market.get("price")
    revenue = market.get("revenue") or {}
    yield_t_ha = revenue.get("yield_t_ha")
    if yield_t_ha is None:
        yield_t_ha = (load_requirements().get(crop) or {}).get("target_yield_t_ha")

    assumptions_en, assumptions_mr = _assumptions(factors)

    if not price:
        return {
            "available": False, "crop": crop, "scenario": scenario,
            "reason_en": "No notified market price for this crop, so net profit cannot be estimated.",
            "reason_mr": "या पिकासाठी जाहीर बाजारभाव नाही, त्यामुळे निव्वळ नफा काढता येत नाही.",
            "assumptions_en": assumptions_en, "assumptions_mr": assumptions_mr,
        }
    if not yield_t_ha:
        return {
            "available": False, "crop": crop, "scenario": scenario,
            "reason_en": "No reference target yield on file for this crop.",
            "reason_mr": "या पिकासाठी संदर्भ लक्ष्य उत्पादन उपलब्ध नाही.",
            "assumptions_en": assumptions_en, "assumptions_mr": assumptions_mr,
        }

    price_used = _num(overrides.get("price"), _num(price)) * factors["price_factor"]
    yield_used = _num(overrides.get("yield_t_ha"), _num(yield_t_ha)) * factors["yield_factor"]
    area = _num(area_acres) if area_acres else None

    revenue_ha = price_used * yield_used * 10  # Rs/quintal x (t/ha -> 10 quintals/ha)
    revenue_acre = revenue_ha / ACRES_PER_HA

    costs = crop_costs(crop)
    fert_cost_ha = _num(((fertilizer or {}).get("cost") or {}).get("standard", {}).get("cost_per_ha"))
    if overrides.get("fertilizer_cost_factor") is not None:
        fert_cost_ha *= _num(overrides["fertilizer_cost_factor"], 1.0)
    spray_cost_ha = _num(((spray or {}).get("totals") or {}).get("per_ha"))
    rate = _num(irrigation_cost_per_ha) if irrigation_cost_per_ha is not None else irrigation_rate()
    if overrides.get("irrigation_cost_per_ha") is not None:
        rate = _num(overrides["irrigation_cost_per_ha"])
    irrigation_cost_ha = _num(irrigation_events) * rate

    breakdown = []
    total_cost_ha = 0.0
    for key, per_ha in (
        ("seed", costs["seed"]),
        ("fertilizer", fert_cost_ha),
        ("spray", spray_cost_ha),
        ("irrigation", irrigation_cost_ha),
        ("labor", costs["labor"]),
        ("other", costs["other"]),
    ):
        per_ha = _num(per_ha)
        total_cost_ha += per_ha
        label_en, label_mr = COST_LABELS[key]
        breakdown.append({
            "key": key, "label_en": label_en, "label_mr": label_mr,
            "per_ha": round(per_ha), "per_acre": round(per_ha / ACRES_PER_HA),
            "total": round(per_ha / ACRES_PER_HA * area) if area else None,
        })

    total_cost_acre = total_cost_ha / ACRES_PER_HA
    net_ha = revenue_ha - total_cost_ha
    net_acre = net_ha / ACRES_PER_HA

    return {
        "available": True, "crop": crop, "scenario": scenario,
        "scenario_label_en": factors["label_en"], "scenario_label_mr": factors["label_mr"],
        "price": round(price_used, 2), "yield_t_ha": round(yield_used, 2),
        "irrigation_events": irrigation_events, "area_acres": area,
        "revenue": {
            "per_ha": round(revenue_ha), "per_acre": round(revenue_acre),
            "total": round(revenue_acre * area) if area else None,
        },
        "costs": {
            "per_ha": round(total_cost_ha), "per_acre": round(total_cost_acre),
            "total": round(total_cost_acre * area) if area else None,
        },
        "breakdown": breakdown,
        "net": {
            "per_ha": round(net_ha), "per_acre": round(net_acre),
            "total": round(net_acre * area) if area else None,
        },
        "margin_pct": round(net_ha / revenue_ha * 100) if revenue_ha else 0,
        "assumptions_en": assumptions_en, "assumptions_mr": assumptions_mr,
        "disclaimer_en": "Estimate only. Confirm your local input costs, actual yield and selling rate before committing.",
        "disclaimer_mr": "हा फक्त अंदाज आहे. ठरवण्यापूर्वी स्थानिक खर्च, प्रत्यक्ष उत्पादन व विक्री दर तपासा.",
    }


def scenarios_for(crop, area_acres=None, market=None, fertilizer=None, spray=None,
                  irrigation_events=None, overrides=None):
    """Best / expected / worst estimate for one crop in a single call."""
    return {
        name: profitability(
            crop, area_acres=area_acres, market=market, fertilizer=fertilizer, spray=spray,
            irrigation_events=irrigation_events, scenario=name, overrides=overrides,
        )
        for name in scenario_names()
    }


def compare_profitability(crops, area_acres=None, contexts=None, scenario="expected"):
    """Rank crops by estimated net profit per acre. `contexts` maps crop -> {market, fertilizer, spray, irrigation_events}."""
    contexts = contexts or {}
    rows = []
    for crop in crops:
        context = contexts.get(crop) or {}
        estimate = profitability(
            crop, area_acres=area_acres, market=context.get("market"),
            fertilizer=context.get("fertilizer"), spray=context.get("spray"),
            irrigation_events=context.get("irrigation_events"), scenario=scenario,
        )
        rows.append(estimate)
    rows.sort(key=lambda row: (not row.get("available"), -(row.get("net", {}).get("per_acre") or 0)))
    return rows
