"""Farm expense manager: categories, validation, summaries, budgets and
actual-vs-estimate reconciliation against the crop economics service.

Expenses are stored as ``FarmRecord`` rows with ``kind == "expense"`` so that
they live alongside soil tests, harvests and irrigation logs (farm history) and
are picked up by the existing analytics and history services. The new dedicated
columns (category, season, vendor, payment_method) are additive and nullable, so
older expense rows written before this feature keep working through the
``data_json`` fallback.
"""

from datetime import date

# --------------------------------------------------------------------- lookups

# category key -> (English label, Marathi label)
EXPENSE_CATEGORIES = {
    "seeds": ("Seeds & seedlings", "बियाणे व रोपे"),
    "fertilizers": ("Fertilizers & soil amendments", "खते व माती सुधारके"),
    "pesticides": ("Pesticides & crop protection", "कीटकनाशके व पीक संरक्षण"),
    "labor": ("Labour", "मजुरी"),
    "irrigation": ("Irrigation & water", "सिंचन व पाणी"),
    "energy": ("Electricity & fuel", "वीज व इंधन"),
    "equipment": ("Equipment & machinery", "यंत्रे व यंत्रणा"),
    "land": ("Land preparation", "जमीन तयारी"),
    "transport": ("Transportation", "वाहतूक"),
    "other": ("Other expenses", "इतर खर्च"),
}

PAYMENT_METHODS = {
    "cash": ("Cash", "रोख"),
    "upi": ("UPI", "UPI"),
    "bank": ("Bank transfer", "बँक हस्तांतरण"),
    "card": ("Card", "कार्ड"),
    "credit": ("Credit / on account", "उधारी"),
    "other": ("Other", "इतर"),
}

# Maps an expense category onto the economics cost line it replaces, so recorded
# spend can stand in for (not add to) the matching planning estimate.
CATEGORY_TO_COST_KEY = {
    "seeds": "seed",
    "fertilizers": "fertilizer",
    "pesticides": "spray",
    "irrigation": "irrigation",
    "labor": "labor",
    "energy": "other",
    "equipment": "other",
    "land": "other",
    "transport": "other",
    "other": "other",
}


def category_label(key, language="en"):
    en, mr = EXPENSE_CATEGORIES.get(key, (key, key))
    return mr if language == "mr" else en


def payment_label(key, language="en"):
    en, mr = PAYMENT_METHODS.get(key, (key, key))
    return mr if language == "mr" else en


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------- validation

def validate(payload):
    """Validate an expense form. Returns ``(cleaned, errors)``.

    ``errors`` maps field name -> bilingual-ready message key; callers translate.
    A positive amount, a known category and a parseable date are required.
    """
    errors = {}

    category = (payload.get("category") or "").strip().lower()
    if category not in EXPENSE_CATEGORIES:
        errors["category"] = "unknown category"

    amount = _num(payload.get("amount"))
    if amount is None:
        errors["amount"] = "amount is required"
    elif amount <= 0:
        errors["amount"] = "amount must be greater than zero"
    elif amount > 100_000_000:
        errors["amount"] = "amount is unrealistically large"

    recorded_on = _parse_date(payload.get("recorded_on"))
    if payload.get("recorded_on") and recorded_on is None:
        errors["recorded_on"] = "date is not valid"
    if recorded_on is None:
        recorded_on = date.today()
    if recorded_on > date.today():
        errors["recorded_on"] = "date cannot be in the future"

    quantity = _num(payload.get("quantity"))
    if quantity is not None and quantity < 0:
        errors["quantity"] = "quantity cannot be negative"

    payment = (payload.get("payment_method") or "").strip().lower() or None
    if payment and payment not in PAYMENT_METHODS:
        errors["payment_method"] = "unknown payment method"

    cleaned = {
        "category": category,
        "amount": round(amount, 2) if amount is not None else None,
        "recorded_on": recorded_on,
        "crop": (payload.get("crop") or "").strip()[:60] or None,
        "season": (payload.get("season") or "").strip()[:40] or None,
        "vendor": (payload.get("vendor") or "").strip()[:120] or None,
        "payment_method": payment,
        "quantity": round(quantity, 3) if quantity is not None else None,
        "unit": (payload.get("unit") or "").strip()[:20] or None,
        "note": (payload.get("note") or "").strip()[:1000] or None,
    }
    return cleaned, errors


def apply_to_record(record, cleaned):
    """Copy validated expense fields onto a FarmRecord row (create or edit)."""
    record.kind = "expense"
    record.recorded_on = cleaned["recorded_on"]
    record.category = cleaned["category"]
    record.season = cleaned["season"]
    record.vendor = cleaned["vendor"]
    record.payment_method = cleaned["payment_method"]
    record.amount = cleaned["amount"]
    record.quantity = cleaned["quantity"]
    record.unit = cleaned["unit"]
    record.crop = cleaned["crop"]
    record.note = cleaned["note"]
    record.data_json = _dump_legacy(cleaned)
    return record


def _dump_legacy(cleaned):
    import json

    return json.dumps(
        {
            "category": cleaned["category"],
            "season": cleaned["season"],
            "vendor": cleaned["vendor"],
            "payment_method": cleaned["payment_method"],
        }
    )


# --------------------------------------------------------------------- summaries

def _expense_records(records):
    return [record for record in records if record.kind == "expense"]


def summary(records, today=None):
    """Totals, category/crop/season breakdowns, monthly trend and recent rows."""
    today = today or date.today()
    rows = _expense_records(records)

    by_category = {}
    by_crop = {}
    by_season = {}
    by_month = {}
    total = 0.0
    current_month = 0.0
    recent = []

    for record in rows:
        amount = _num(record.amount) or 0.0
        category = record.expense_category if hasattr(record, "expense_category") else (
            getattr(record, "category", None) or (record.data.get("category") if hasattr(record, "data") else None) or "other"
        )
        season = getattr(record, "expense_season", None) or getattr(record, "season", None)
        vendor = getattr(record, "expense_vendor", None) or getattr(record, "vendor", None)

        total += amount
        by_category[category] = by_category.get(category, 0.0) + amount
        if record.crop:
            by_crop[record.crop] = by_crop.get(record.crop, 0.0) + amount
        if season:
            by_season[season] = by_season.get(season, 0.0) + amount
        when = record.recorded_on
        if when:
            key = when.strftime("%Y-%m")
            by_month[key] = by_month.get(key, 0.0) + amount
            if when.year == today.year and when.month == today.month:
                current_month += amount
        recent.append(
            {
                "id": record.id,
                "date": when,
                "category": category,
                "amount": amount,
                "crop": record.crop,
                "season": season,
                "vendor": vendor,
                "payment_method": getattr(record, "payment_method", None),
                "quantity": getattr(record, "quantity", None),
                "unit": getattr(record, "unit", None),
                "note": getattr(record, "note", None),
            }
        )

    recent.sort(key=lambda row: (row["date"] or date.min), reverse=True)
    month_series = _month_series(by_month, today, months=6)

    categories = sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "count": len(rows),
        "total": round(total),
        "current_month": round(current_month),
        "by_category": {k: round(v) for k, v in categories},
        "top_categories": [{"category": k, "amount": round(v)} for k, v in categories[:5]],
        "by_crop": [{"crop": k, "amount": round(v)} for k, v in
                    sorted(by_crop.items(), key=lambda kv: kv[1], reverse=True)],
        "by_season": [{"season": k, "amount": round(v)} for k, v in
                      sorted(by_season.items(), key=lambda kv: kv[1], reverse=True)],
        "by_month": month_series,
        "recent": recent[:12],
        "has_data": bool(rows),
    }


def _month_series(by_month, today, months=6):
    series = []
    year, month = today.year, today.month
    keys = []
    for _ in range(months):
        keys.append((year, month))
        month -= 1
        if month < 1:
            month = 12
            year -= 1
    for y, m in reversed(keys):
        key = f"{y:04d}-{m:02d}"
        series.append({"key": key, "month": m, "year": y, "amount": round(by_month.get(key, 0.0))})
    return series


# --------------------------------------------------------------------- budgets

def budget_status(budgets, expense_summary):
    """Compare each budget against recorded spend for its scope."""
    rows = []
    any_over = False
    any_warn = False
    for budget in budgets:
        spent = _spent_for_scope(budget, expense_summary)
        amount = _num(budget.amount) or 0.0
        pct = round(spent / amount * 100, 1) if amount else 0.0
        warn_pct = budget.warn_pct or 80
        if amount and spent > amount:
            status = "over"
            any_over = True
        elif amount and pct >= warn_pct:
            status = "warn"
            any_warn = True
        else:
            status = "ok"
        rows.append(
            {
                "id": budget.id,
                "scope": budget.scope,
                "crop": budget.crop,
                "season": budget.season,
                "amount": round(amount),
                "warn_pct": warn_pct,
                "spent": round(spent),
                "remaining": round(amount - spent),
                "pct": pct,
                "status": status,
            }
        )
    rows.sort(key=lambda row: {"over": 0, "warn": 1, "ok": 2}.get(row["status"], 3))
    return {"rows": rows, "any_over": any_over, "any_warn": any_warn, "has_budgets": bool(rows)}


def _spent_for_scope(budget, expense_summary):
    if budget.scope == "crop":
        for row in expense_summary.get("by_crop", []):
            if row["crop"] == budget.crop:
                return row["amount"]
        return 0.0
    if budget.scope == "season":
        for row in expense_summary.get("by_season", []):
            if row["season"] == budget.season:
                return row["amount"]
        return 0.0
    return expense_summary.get("total", 0.0)


# --------------------------------------------------------------------- profit

def actual_vs_estimate(estimate, expense_summary, harvest=None):
    """Reconcile recorded expenses with a crop economics estimate.

    Recorded spend replaces the matching estimated cost line (by category); cost
    lines with no recorded spend fall back to the planning estimate, so nothing is
    double-counted. Net profit is only labelled *actual* when both recorded
    revenue and recorded expenses exist.
    """
    expense_summary = expense_summary or {}
    harvest = harvest or {}

    if not estimate or not estimate.get("available"):
        return {
            "available": False,
            "has_estimate": False,
            "reason_en": "No profitability estimate for this crop yet.",
            "reason_mr": "या पिकासाठी अद्याप नफा अंदाज नाही.",
        }

    area = _num(estimate.get("area_acres"))
    estimated_total = (estimate.get("costs") or {}).get("total")
    if estimated_total is None and area:
        estimated_total = round((estimate.get("costs") or {}).get("per_acre", 0) * area)
    estimated_total = _num(estimated_total) or 0.0

    # Sum recorded spend per economics cost key.
    actual_by_key = {}
    for category, amount in (expense_summary.get("by_category") or {}).items():
        key = CATEGORY_TO_COST_KEY.get(category, "other")
        actual_by_key[key] = actual_by_key.get(key, 0.0) + _num(amount)
    actual_total = sum(actual_by_key.values())

    rows = []
    projected_total = 0.0
    for line in estimate.get("breakdown", []):
        key = line["key"]
        estimated_line = _num(line.get("total"))
        if estimated_line is None and area:
            estimated_line = round((line.get("per_acre") or 0) * area)
        actual_line = actual_by_key.get(key)
        if actual_line:
            used = actual_line
            source = "actual"
        else:
            used = _num(estimated_line) or 0.0
            source = "estimate"
        projected_total += used
        rows.append(
            {
                "key": key,
                "label_en": line.get("label_en"),
                "label_mr": line.get("label_mr"),
                "estimated": round(_num(estimated_line) or 0),
                "actual": round(actual_line) if actual_line else None,
                "used": round(used),
                "source": source,
            }
        )

    revenue_actual = _num(harvest.get("total_revenue"))
    revenue_estimated = _num((estimate.get("revenue") or {}).get("total"))
    if revenue_actual:
        revenue_used = revenue_actual
        revenue_source = "recorded_harvest"
    else:
        revenue_used = revenue_estimated
        revenue_source = "estimate"

    has_actual_expenses = actual_total > 0
    has_revenue = bool(revenue_actual)

    net_projected = (revenue_used - projected_total) if revenue_used is not None else None
    net_actual = (revenue_actual - actual_total) if (has_actual_expenses and has_revenue) else None

    return {
        "available": True,
        "has_estimate": True,
        "has_actual_expenses": has_actual_expenses,
        "has_revenue": has_revenue,
        "actual_expenses_total": round(actual_total),
        "estimated_total": round(estimated_total),
        "projected_total": round(projected_total),
        "rows": rows,
        "revenue": {
            "actual": round(revenue_actual) if revenue_actual is not None else None,
            "estimated": round(revenue_estimated) if revenue_estimated is not None else None,
            "used": round(revenue_used) if revenue_used is not None else None,
            "source": revenue_source,
        },
        "net": {
            "actual": round(net_actual) if net_actual is not None else None,
            "projected": round(net_projected) if net_projected is not None else None,
            "available": net_actual is not None,
            "margin_actual_pct": round(net_actual / revenue_actual * 100) if net_actual is not None and revenue_actual else None,
        },
        "notes_en": [
            "Recorded expenses replace the matching planning estimate line; categories with no "
            "recorded spend still use the estimate, so nothing is counted twice.",
            "A true net profit needs both a recorded harvest revenue and recorded expenses.",
        ],
        "notes_mr": [
            "नोंदवलेला खर्च त्या खर्चाच्या अंदाजाऐवजी वापरला जातो; ज्याची नोंद नाही तिथे अंदाज वापरला जातो - म्हणून दुहेरी मोजणी होत नाही.",
            "खरा निव्वळ नफा काढण्यासाठी नोंदवलेले कापणी उत्पन्न आणि नोंदवलेला खर्च दोन्ही आवश्यक आहे.",
        ],
    }
