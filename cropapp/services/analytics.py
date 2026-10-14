"""Cross-farm analytics for the dashboard.

Aggregates every farm's tasks, records and recommendations into a small set of
series the analytics page renders with Chart.js. Pure reads - no network.
"""

from datetime import date

from cropapp.services import history as history_service

MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_MR = ["जाने", "फेब्रु", "मार्च", "एप्रि", "मे", "जून", "जुलै", "ऑग", "सप्टें", "ऑक्टो", "नोव्हें", "डिसें"]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build(user, language="en"):
    farms = list(getattr(user, "farms", []) or [])
    months = MONTHS_MR if language == "mr" else MONTHS_EN
    today = date.today()

    acres = 0.0
    crop_acres = {}
    soil = []
    tasks_done = tasks_pending = 0
    tasks_by_kind = {}
    expense_by_category = {}
    expense_total = 0.0
    expense_by_month = {}
    harvest_by_crop = {}
    harvest_total = 0.0
    harvest_qty = 0.0

    for farm in farms:
        area = _num(farm.area_acres) or 0.0
        acres += area
        if farm.crop:
            crop_acres[farm.crop] = crop_acres.get(farm.crop, 0.0) + (area or 1.0)
        if any(v is not None for v in (farm.soil_n, farm.soil_p, farm.soil_k)):
            soil.append({
                "name": farm.name, "crop": farm.crop,
                "acres": round(area, 2) if area else None,
                "n": farm.soil_n, "p": farm.soil_p, "k": farm.soil_k, "ph": farm.soil_ph,
            })

        for task in farm.tasks:
            bucket = tasks_by_kind.setdefault(task.kind or "other", {"done": 0, "pending": 0})
            if task.status == "done":
                tasks_done += 1
                bucket["done"] += 1
            else:
                tasks_pending += 1
                bucket["pending"] += 1

        for record in farm.records:
            amount = _num(record.amount) or 0.0
            if record.kind == "expense":
                category = (record.data.get("category") or "other").strip().lower()
                expense_by_category[category] = expense_by_category.get(category, 0.0) + amount
                expense_total += amount
                if record.recorded_on:
                    key = months[record.recorded_on.month - 1]
                    expense_by_month[key] = expense_by_month.get(key, 0.0) + amount
            elif record.kind == "harvest":
                crop = record.crop or "other"
                harvest_by_crop[crop] = harvest_by_crop.get(crop, 0.0) + amount
                harvest_total += amount
                harvest_qty += _num(record.quantity) or 0.0

    crop_rows = sorted(
        ({"crop": crop, "acres": round(area, 2)} for crop, area in crop_acres.items()),
        key=lambda row: row["acres"], reverse=True,
    )

    return {
        "kpis": {
            "farms": len(farms),
            "acres": round(acres, 1),
            "crops": len(crop_acres),
            "tasks_done": tasks_done,
            "tasks_pending": tasks_pending,
            "expense_total": round(expense_total),
            "harvest_total": round(harvest_total),
            "harvest_qty_kg": round(harvest_qty, 1),
        },
        "crops": crop_rows,
        "tasks": {
            "done": tasks_done,
            "pending": tasks_pending,
            "by_kind": [{"kind": k, **v} for k, v in sorted(tasks_by_kind.items())],
        },
        "expenses": {
            "total": round(expense_total),
            "by_category": [{"category": k, "amount": round(v)} for k, v in
                            sorted(expense_by_category.items(), key=lambda kv: kv[1], reverse=True)],
            "by_month": [{"month": m, "amount": round(expense_by_month.get(m, 0.0))} for m in months],
        },
        "harvest": {
            "total": round(harvest_total),
            "quantity_kg": round(harvest_qty, 1),
            "by_crop": [{"crop": k, "revenue": round(v)} for k, v in
                        sorted(harvest_by_crop.items(), key=lambda kv: kv[1], reverse=True)],
        },
        "soil": soil,
        "has_data": bool(farms),
        "generated_on": today.isoformat(),
    }
