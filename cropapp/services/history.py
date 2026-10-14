"""Farm history summaries.

Reads the FarmRecord rows for a farm and turns them into: soil pH / nutrient trends,
an expense total by category, harvest totals, and an actual-vs-predicted comparison
against the farm's latest stored recommendation. All read-only and defensive - a
missing field simply drops that point rather than raising.
"""


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def soil_series(records):
    """Chronological list of {date, ph, n, p, k} soil-test points."""
    series = []
    for record in records:
        if record.kind != "soil":
            continue
        data = record.data
        series.append({
            "date": record.recorded_on,
            "ph": _num(data.get("ph")),
            "n": _num(data.get("n")),
            "p": _num(data.get("p")),
            "k": _num(data.get("k")),
        })
    series.sort(key=lambda point: point["date"] or __import__("datetime").date.min)
    return series


def expense_summary(records):
    """Total expenses and a per-category breakdown."""
    by_category = {}
    total = 0.0
    for record in records:
        if record.kind != "expense":
            continue
        amount = _num(record.amount) or 0.0
        # Prefer the dedicated column (new rows); fall back to the legacy JSON blob
        # so expense rows written before the expense manager keep working.
        category = getattr(record, "category", None) or record.data.get("category") or "other"
        category = str(category).strip().lower()
        by_category[category] = by_category.get(category, 0.0) + amount
        total += amount
    return {"by_category": by_category, "total": round(total)}


def harvest_summary(records):
    """Harvest entries: count, total quantity, total revenue and the latest entry."""
    items = []
    total_quantity = 0.0
    total_revenue = 0.0
    for record in records:
        if record.kind != "harvest":
            continue
        quantity = _num(record.quantity)
        revenue = _num(record.amount)
        total_quantity += quantity or 0.0
        total_revenue += revenue or 0.0
        items.append({
            "id": record.id, "date": record.recorded_on, "crop": record.crop,
            "quantity": quantity, "unit": record.unit, "revenue": revenue,
            "sell_price": _num(record.data.get("price")), "note": record.note,
        })
    items.sort(key=lambda item: item["date"] or __import__("datetime").date.min, reverse=True)
    return {
        "items": items,
        "count": len(items),
        "total_quantity": round(total_quantity, 1),
        "total_revenue": round(total_revenue),
        "latest": items[0] if items else None,
    }


def actual_vs_predicted(recommendation, harvest):
    """Compare a stored recommendation's estimate with a recorded harvest.

    Returns None when either side is missing. Percentages are signed (negative = below
    prediction). Comparison uses totals only when a farm area is known, otherwise it
    compares the per-hectare figures.
    """
    if recommendation is None or harvest is None:
        return None
    market = recommendation.msp or {}
    revenue = market.get("revenue") or {}
    predicted_revenue = _num(revenue.get("total"))
    predicted_yield_t = _num(revenue.get("yield_t_ha"))
    predicted_price = _num(market.get("price"))
    area = _num(revenue.get("area_acres"))

    actual_revenue = _num(harvest.get("revenue"))
    actual_quantity = _num(harvest.get("quantity"))
    actual_price = _num(harvest.get("sell_price"))
    actual_yield_t = None
    if actual_quantity is not None and area:
        actual_yield_t = round(actual_quantity / 1000.0 / area, 2)

    def _delta(actual, predicted):
        if actual is None or not predicted:
            return None
        return round((actual - predicted) / predicted * 100.0, 1)

    return {
        "available": predicted_revenue is not None and actual_revenue is not None,
        "crop": recommendation.crop,
        "area_acres": area,
        "revenue": {
            "predicted": predicted_revenue, "actual": actual_revenue,
            "delta_pct": _delta(actual_revenue, predicted_revenue),
        },
        "yield_t_ha": {
            "predicted": predicted_yield_t, "actual": actual_yield_t,
            "delta_pct": _delta(actual_yield_t, predicted_yield_t),
        },
        "price": {
            "predicted": predicted_price, "actual": actual_price,
            "delta_pct": _delta(actual_price, predicted_price),
        },
        "note_en": "Comparison is against the estimate stored when the recommendation was saved; "
                   "it does not retrain the model.",
        "note_mr": "ही तुलना शिफारस जतन करताना ठेवलेल्या अंदाजाशी आहे; त्यामुळे मॉडेल पुन्हा प्रशिक्षित होत नाही.",
    }
