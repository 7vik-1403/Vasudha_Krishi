from datetime import date, datetime, timedelta

from cropapp.models import FarmTask
from cropapp.services.datasets import calendar_for, load_vegetables
from cropapp.services import water as water_service

DEV_DIGITS = str.maketrans("0123456789.", "०१२३४५६७८९.")

ACRES_PER_HA = 2.47105
MAX_IRRIGATION_TASKS = 12


def mr_number(value):
    return str(value).translate(DEV_DIGITS)


def _round(value):
    return round(value, 1)


def _due(sowing, day):
    if isinstance(sowing, datetime):
        sowing = sowing.date()
    return sowing + timedelta(days=int(day or 0))


def build_tasks(farm, recommendation, sowing=None, user_language="en"):
    """Generate dated fertilizer / pest / irrigation / harvest tasks for one saved recommendation."""
    crop = (recommendation.crop or "").strip().lower()
    calendar = calendar_for(crop) or {}
    if not calendar:
        calendar = _calendar_from_vegetable(crop)
    fertilizer = recommendation.fertilizer or {}
    sowing = sowing or date.today()
    if isinstance(sowing, str):
        sowing = date.fromisoformat(sowing)
    try:
        water = recommendation.water
    except AttributeError:
        water = None

    tasks = build_schedule(
        crop,
        fertilizer,
        water,
        calendar,
        sowing,
        water_source=getattr(farm, "water_source", None),
        water_days=getattr(farm, "water_days", None),
        rainfall_mm=getattr(farm, "rainfall_mm", None),
    )
    for task in tasks:
        yield FarmTask(
            user_id=farm.user_id,
            farm_id=farm.id,
            recommendation_id=recommendation.id,
            status="pending",
            **task,
        )


def build_schedule(crop, fertilizer, water, calendar, sowing,
                   water_source=None, water_days=None, rainfall_mm=None):
    """Return the dated task list as plain dicts (no DB), reused by the scheduler and the preview page."""
    crop = (crop or "").strip().lower()
    calendar = calendar or calendar_for(crop) or _calendar_from_vegetable(crop)
    fertilizer = fertilizer or {}
    if isinstance(sowing, str):
        sowing = date.fromisoformat(sowing)
    sowing = sowing or date.today()

    tasks = []
    for stage in fertilizer.get("plan") or []:
        doses = []
        mr_doses = []
        for product, key in (("Urea", "urea"), ("DAP", "dap"), ("MOP", "mop")):
            kg = stage.get(f"{key}_kg_ha") or 0
            if kg > 0:
                doses.append(f"{product} {kg} kg/ha")
                mr_doses.append(f"{_product_mr(product)} {mr_number(kg)} किग्रॅ/हेक्टर")
        if not doses:
            continue
        per_acre = stage.get("per_acre") or {}
        detail = (
            f"Split dose ({int((stage.get('n_share') or 0) * 100)}% N / {int((stage.get('p_share') or 0) * 100)}% P / "
            f"{int((stage.get('k_share') or 0) * 100)}% K of the total plan): " + ", ".join(doses)
            + ". Per acre: "
            + ", ".join(
                f"{product} {per_acre.get(key, 0)} kg"
                for product, key in (("Urea", "urea"), ("DAP", "dap"), ("MOP", "mop"))
                if per_acre.get(key, 0)
            )
            + "."
        )
        detail_mr = (
            "स्प्लिट अंतरे: " + ", ".join(mr_doses) + ". प्रति एकर: "
            + ", ".join(
                f"{_product_mr(product)} {mr_number(per_acre.get(key, 0))} किग्रॅ"
                for product, key in (("युरिया", "urea"), ("डीएपी", "dap"), ("एमओपी", "mop"))
                if per_acre.get(key, 0)
            )
            + "."
        )
        tasks.append(
            dict(
                kind="fertilizer",
                title_en=f"{stage.get('stage_en') or 'Fertilizer application'}: " + ", ".join(doses),
                title_mr=(stage.get("stage_mr") or "खत टाकणी") + ": " + ", ".join(mr_doses),
                detail_en=detail,
                detail_mr=detail_mr,
                due_date=_due(sowing, stage.get("day")),
                source="schedule",
            )
        )

    for window in calendar.get("pests") or []:
        tasks.append(
            dict(
                kind="pesticide" if window.get("kind") != "disease" else "spray",
                title_en=f"Scout for {window.get('en')}",
                title_mr=(window.get("mr") or "कीटक निरीक्षण"),
                detail_en=(
                    f"Day {window.get('from')}-{window.get('to')} after sowing. Control: {window.get('control_en')}"
                ),
                detail_mr=(
                    f"रोपणीनंतर दिवस {mr_number(window.get('from'))}-{mr_number(window.get('to'))}. "
                    f"उपाय: {window.get('control_mr') or window.get('control_en')}"
                ),
                due_date=_due(sowing, window.get("from")),
                source="schedule",
            )
        )

    tasks.extend(
        _irrigation_tasks(
            crop, calendar, water, sowing,
            water_source=water_source, water_days=water_days, rainfall_mm=rainfall_mm,
        )
    )

    if calendar.get("duration_days"):
        tasks.append(
            dict(
                kind="harvest",
                title_en=f"Expected maturity / harvest (~{calendar.get('duration_days')} days)",
                title_mr=f"अपेक्षित पिक पकणे (~{mr_number(calendar.get('duration_days'))} दिवस)",
                detail_en=f"Sow around {sowing.strftime('%d %b %Y')}; plan harvest and market linkage by "
                f"{(sowing + timedelta(days=int(calendar.get('duration_days')))).strftime('%d %b %Y')}.",
                detail_mr=f"रोपणी: {sowing.strftime('%d %b %Y')}; पिक कापणीसाठी अंदाजे "
                f"{(sowing + timedelta(days=int(calendar.get('duration_days')))).strftime('%d %b %Y')}.",
                due_date=_due(sowing, calendar.get("duration_days")),
                source="schedule",
            )
        )

    return tasks


def _calendar_from_vegetable(crop):
    """Shape a bundled vegetable entry like a crop calendar so the scheduler can use it."""
    veg = load_vegetables().get(crop)
    if not veg:
        return {}
    pests = [
        {
            "from": int(window.get("day") or 30),
            "to": int(window.get("day") or 30) + 10,
            "kind": "insecticide",
            "en": window.get("en"),
            "mr": window.get("mr"),
            "control_en": window.get("control_en"),
            "control_mr": window.get("control_mr"),
        }
        for window in veg.get("pests") or []
    ]
    interval = int(veg.get("interval_days") or 6)
    duration = int(veg.get("duration_days") or 90)
    months = [int(m) for m in veg.get("sowing_months", [])]
    months_txt = ", ".join(str(m) for m in months) if months else "the right season"
    months_txt_mr = ", ".join(mr_number(m) for m in months) if months else "योग्य हंगाम"
    return {
        "label_en": veg.get("en"),
        "label_mr": veg.get("mr"),
        "duration_days": veg.get("duration_days"),
        "pests": pests,
        "sowing_en": f"Sow in month(s) {months_txt}; harvest about {duration} days after sowing.",
        "sowing_mr": f"महिना {months_txt_mr} मध्ये पेरणी; पेरणीनंतर सुमारे {mr_number(duration)} दिवसांनी कापणी.",
        "stages": [
            {"day": 0, "en": "Sowing / germination", "mr": "पेरणी / उगवण"},
            {"day": max(1, duration // 3), "en": "Vegetative growth", "mr": "वाढीचा टप्पा"},
            {"day": max(1, (duration * 2) // 3), "en": "Flowering / fruiting", "mr": "फुलोरा / फळधारणा"},
            {"day": duration, "en": "Harvest", "mr": "कापणी"},
        ],
        "irrigation_en": f"Light irrigations about every {interval} days - keep the root zone evenly moist, "
        f"never waterlogged. Spacing: {veg.get('spacing', 'as advised')}.",
        "irrigation_mr": f"अंदाजे दर {interval} दिवसांनी हलकी सिंचन - मुळांचा भाग ओला ठेवा, पाणी थांबू न देता. "
        f"अंतर: {veg.get('spacing', 'सुचवलेल्या प्रमाणे')}.",
    }


def _irrigation_tasks(crop, calendar, water, sowing, water_source=None, water_days=None, rainfall_mm=None):
    """Dated irrigation tasks based on the water the farmer says they have."""
    report = water or water_service.evaluate(
        crop,
        source=water_source,
        days=water_days,
        rainfall_normal=rainfall_mm,
    )
    plan = (calendar or {}).get("irrigation_en")
    plan_mr = (calendar or {}).get("irrigation_mr") or plan

    if not report.get("known"):
        if not plan:
            return []
        return [
            dict(
                kind="irrigation",
                title_en="Irrigation plan for this crop",
                title_mr="या पिकासाठी सिंचन योजना",
                detail_en=plan,
                detail_mr=plan_mr,
                due_date=_due(sowing, 0),
                source="schedule",
            )
        ]

    if (report.get("days_per_week") or 0) <= 0:
        detail = (
            f"Rain-fed field: no irrigation supply reported. {report.get('advice_en', '')}"
            + (f" Crop guidance: {plan}" if plan else "")
        ).strip()
        detail_mr = (
            f"पाऊसावर अवलंबून शेत: सिंचन स्रोत नोंदवलेला नाही. {report.get('advice_mr', '')}"
            + (f" पिक सूचना: {plan_mr}" if plan_mr else "")
        ).strip()
        return [
            dict(
                kind="irrigation",
                title_en="No irrigation arranged - watch the rainfall",
                title_mr="सिंचन व्यवस्था नाही - पावसावर लक्ष ठेवा",
                detail_en=detail,
                detail_mr=detail_mr,
                due_date=_due(sowing, 0),
                source="schedule",
            )
        ]

    interval = int(report.get("interval_days") or 7)
    duration = int((calendar or {}).get("duration_days") or 100)
    out = []
    day = interval
    while day <= duration and len(out) < MAX_IRRIGATION_TASKS:
        detail = (
            f"Irrigation #{len(out) + 1}: about {interval} days after the previous one. "
            f"Source {report.get('source_label', 'irrigation')}, "
            f"{report.get('days_per_week')} days a week available. "
            + (f"Stage guidance: {plan} " if plan else "")
            + report.get("advice_en", "")
        )
        detail_mr = (
            f"सिंचन #{len(out) + 1}: मागील सिंचनापासून अंदाजे {mr_days(interval)} दिवस. "
            f"स्रोत: {report.get('source_label_mr', 'सिंचन')}, "
            f"आठवड्यातून {mr_days(report.get('days_per_week'))} दिवस उपलब्ध. "
            + (f"टप्पा सूचना: {plan_mr} " if plan_mr else "")
            + report.get("advice_mr", "")
        )
        out.append(
            dict(
                kind="irrigation",
                title_en=f"Irrigation #{len(out) + 1} - {crop}",
                title_mr=f"सिंचन #{len(out) + 1} - {crop}",
                detail_en=detail,
                detail_mr=detail_mr,
                due_date=_due(sowing, day),
                source="schedule",
            )
        )
        day += interval

    if not out and plan:
        out.append(
            dict(
                kind="irrigation",
                title_en="Irrigation plan for this crop",
                title_mr="या पिकासाठी सिंचन योजना",
                detail_en=plan,
                detail_mr=plan_mr,
                due_date=_due(sowing, 0),
                source="schedule",
            )
        )
    return out


def mr_days(value):
    return mr_number(value)


def _product_mr(name):
    return {"Urea": "युरिया", "DAP": "डीएपी", "MOP": "एमओपी"}.get(name, name)
