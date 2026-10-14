"""Spray / plant-protection planner.

Turns the pest windows in the crop calendar into named products with a dose and an
indicative cost, and offers a cheaper or bio alternative for each so the farmer can pick.
"""

from cropapp.services.datasets import load_crop_sprays, load_spray_products

ACRES_PER_HA = 2.47105
DEFAULT_APPLICATIONS = 2


def _product(key):
    meta = load_spray_products().get(key)
    if not meta:
        return None
    rate = float(meta.get("rate_ha") or 0)
    unit_price = float(meta.get("price_per_unit") or 0)
    cost_ha = rate * unit_price
    return {
        "key": key,
        "name_en": meta.get("name_en") or key,
        "name_mr": meta.get("name_mr") or key,
        "kind": meta.get("kind"),
        "is_bio": meta.get("kind") == "bio",
        "rate_ha": rate,
        "unit": meta.get("unit"),
        "cost_per_ha": round(cost_ha),
        "cost_per_acre": round(cost_ha / ACRES_PER_HA),
    }


def plan(crop, calendar=None, area_acres=None, applications=DEFAULT_APPLICATIONS):
    """Build the spray plan (per pest window) with primary + alternative product and cost."""
    calendar = calendar or {}
    pests = calendar.get("pests") or []
    mapping = load_crop_sprays().get((crop or "").strip().lower())
    area = float(area_acres) if area_acres else None

    rows = []
    total_primary_ha = 0.0
    total_alt_ha = 0.0
    total_savings_ha = 0.0
    has_cost = False
    for index, pest in enumerate(pests):
        keys = mapping[index] if mapping and index < len(mapping) else None
        primary = _product(keys[0]) if keys else None
        alt = _product(keys[1]) if keys and len(keys) > 1 and keys[1] else None

        if primary:
            has_cost = True
            total_primary_ha += primary["cost_per_ha"]
        if alt:
            total_alt_ha += alt["cost_per_ha"]
        if primary and alt:
            total_savings_ha += max(0.0, primary["cost_per_ha"] - alt["cost_per_ha"])

        rows.append(
            {
                "name_en": pest.get("en"),
                "name_mr": pest.get("mr"),
                "kind": pest.get("kind") or (primary or {}).get("kind"),
                "control_en": pest.get("control_en"),
                "control_mr": pest.get("control_mr"),
                "day": pest.get("day"),
                "from": pest.get("from"),
                "to": pest.get("to"),
                "primary": primary,
                "alt": alt,
                "alt_cheaper": bool(primary and alt and alt["cost_per_ha"] < primary["cost_per_ha"]),
                "savings_per_ha": round(primary["cost_per_ha"] - alt["cost_per_ha"]) if primary and alt else None,
            }
        )

    def totals(per_ha):
        per_acre = per_ha / ACRES_PER_HA if per_ha else 0.0
        season_ha = per_ha * applications
        season_acre = per_acre * applications
        return {
            "per_ha": round(per_ha * applications),
            "per_acre": round(season_acre),
            "total": round(season_acre * area) if area else None,
        }

    primary_totals = totals(total_primary_ha)
    alt_totals = totals(total_alt_ha)
    savings_totals = totals(total_savings_ha)

    return {
        "available": bool(rows),
        "has_cost": has_cost,
        "rows": rows,
        "applications": applications,
        "totals": primary_totals,
        "alt_totals": alt_totals,
        "savings": {
            "per_acre": savings_totals["per_acre"],
            "per_ha": savings_totals["per_ha"],
            "total": savings_totals["total"],
            "pct": round(savings_totals["per_ha"] / primary_totals["per_ha"] * 100) if primary_totals["per_ha"] else 0,
        },
        "area_acres": area,
        "price_source_en": "Indicative retail pack prices (editable in data/spray_products.json)",
        "price_source_mr": "सूचक किरकोळ दर (data/spray_products.json मध्ये बदलता येतात)",
        "disclaimer_en": "Spray costs assume {n} applications per pest window. Always read the product label and follow state agriculture / KVK advice before spraying.".format(n=applications),
        "disclaimer_mr": "फवारणी खर्च प्रत्येक कीड कालावधीसाठी {n} फवारणी गृहीत धरतो. फवारणीपूर्वी उत्पादन लेबल व राज्य कृषी / KVK सल्ला पाळा.".format(n=applications),
    }
