"""Rule-based vegetable suggestions.

Vegetables are not in the ML training set (22 crops), so when the farmer picks the
vegetable group we rank bundled vegetables by sowing window, temperature fit and the
water the farmer actually has.
"""

from cropapp.services.datasets import load_vegetables


def suggest(month, temp_c=None, water_report=None, top=3):
    month = int(month)
    rows = []
    for key, veg in load_vegetables().items():
        window = [int(m) for m in veg.get("sowing_months", [])]
        in_window = month in window
        temp_ok = True
        if temp_c is not None:
            temp_ok = veg.get("temp_min", 0) <= temp_c <= veg.get("temp_max", 100)

        need = veg.get("water_need", "medium")
        water_ok = None
        if water_report and water_report.get("known"):
            water_ok = bool(water_report.get("sufficient")) or (
                need == "low" and water_report.get("days_per_week", 0) > 0
            )

        score = (6 if in_window else 0) + (3 if temp_ok else -3)
        if water_ok is True:
            score += 2
        elif water_ok is False:
            score -= 4

        reasons = []
        reasons_mr = []
        if in_window:
            reasons.append("Sowing window is open this month")
            reasons_mr.append("या महिन्यात पेरणीची खिडकी खुली आहे")
        elif window:
            months_txt = ", ".join(str(m) for m in window)
            reasons.append("Best sowing months: " + months_txt)
            reasons_mr.append("सर्वोत्तम पेरणी महिने: " + months_txt)
        if temp_ok:
            reasons.append(f"Suits {veg.get('temp_min')}-{veg.get('temp_max')} C")
            reasons_mr.append(f"{veg.get('temp_min')}-{veg.get('temp_max')} अंश सेल्सिअस अनुकूल")
        else:
            reasons.append(f"Needs {veg.get('temp_min')}-{veg.get('temp_max')} C - adjust the season")
            reasons_mr.append(f"{veg.get('temp_min')}-{veg.get('temp_max')} अंश सेल्सिअस लागते - हंगाम जुळवा")
        if water_ok is False:
            reasons.append(f"Your water is tight for this {need}-water vegetable")
            need_mr = {"low": "कमी", "medium": "मध्यम", "high": "जास्त"}.get(need, need)
            reasons_mr.append(f"या {need_mr} पाण्याच्या भाजीसाठी तुमचे पाणी कमी आहे")

        rows.append(
            {
                "key": key,
                "name": veg.get("en"),
                "name_mr": veg.get("mr"),
                "sowing_months": window,
                "in_window": in_window,
                "temp_ok": temp_ok,
                "water_ok": water_ok,
                "water_need": need,
                "duration_days": veg.get("duration_days"),
                "interval_days": veg.get("interval_days"),
                "spacing": veg.get("spacing"),
                "npk_kg_ha": veg.get("npk_kg_ha") or {},
                "pests": veg.get("pests") or [],
                "market_en": veg.get("market_en"),
                "market_mr": veg.get("market_mr"),
                "score": score,
                "reasons": reasons,
                "reasons_mr": reasons_mr,
            }
        )

    rows.sort(key=lambda row: (row["in_window"], row["score"]), reverse=True)
    return rows[:top]


def any_in_window(month):
    return any(int(month) in [int(m) for m in veg.get("sowing_months", [])] for veg in load_vegetables().values())
