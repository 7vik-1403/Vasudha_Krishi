import json
from datetime import date, datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from cropapp.extensions import db
from cropapp.ml import predict, recommend
from cropapp.models import Farm, FarmTask, Recommendation
from cropapp.services import suitability as suitability_service
from cropapp.services import vegetables as vegetable_service
from cropapp.services import water as water_service
from cropapp.services.datasets import calendar_for, category_of, crops_in_category, load_categories, msp_for
from cropapp.services.fertilizer import compute_fertilizer, compute_vegetable_fertilizer
from cropapp.services.market import compare_markets, market_for
from cropapp.services.scheduler import _calendar_from_vegetable, build_schedule, build_tasks
from cropapp.services.sprays import plan as spray_plan
from utils.resolver import resolve, resolve_district
from utils.soil import apply_farmer_soil

main_bp = Blueprint("main", __name__)

WATER_SOURCES = list(water_service.SOURCES.keys())


def _float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _t(en_text, mr_text):
    return mr_text if session.get("language", "en") == "mr" else en_text


def _resolve_from_request():
    raw_month = request.form.get("month") or request.args.get("month") or date.today().month
    try:
        month = int(raw_month)
    except (TypeError, ValueError):
        raise ValueError(_t("Choose a valid month (1-12).", "वैध महिना निवडा (१-१२)."))
    district = request.form.get("district") or request.args.get("district")
    lat, lon = _float(request.form.get("lat")), _float(request.form.get("lon"))
    language = session.get("language", "en")
    if lat is not None and lon is not None:
        return resolve(lat, lon, month=month, language=language)
    if district:
        return resolve_district(district, request.form.get("state"), month=month, language=language)
    raise ValueError(_t(
        "Pick a location on the map or choose a district before predicting.",
        "अंदाजापूर्वी नकाशावर ठिकाण निवडा किंवा जिल्हा निवडा.",
    ))


def _water_form():
    source = request.form.get("water_source") or None
    if source and source not in WATER_SOURCES:
        source = None
    return source, _float(request.form.get("water_days"))


def build_recommendation(resolved, form):
    """Resolve -> soil -> water -> (ML crop in a group) or (vegetable list) -> dose, market, calendar."""
    soil = apply_farmer_soil(
        resolved["soil"],
        n=_float(form.get("n")),
        p=_float(form.get("p")),
        k=_float(form.get("k")),
        ph=_float(form.get("ph")),
        language=session.get("language", "en"),
    )
    resolved = dict(resolved)
    resolved["soil"] = soil

    available = soil.get("available") or {}
    features = {
        "N": float(available.get("n") or 0),
        "P": float(available.get("p") or 0),
        "K": float(available.get("k") or 0),
        "temperature": float(resolved["climate"].get("temp_c") or 0),
        "humidity": float(resolved["climate"].get("humidity_pct") or 0),
        "ph": float(soil.get("ph") or 0),
        "rainfall": float(resolved["climate"].get("rainfall_model_mm") or 0),
    }

    category = (form.get("category") or "").strip()
    if category and category not in load_categories():
        category = ""
    source, days = _water_form()
    rainfall_normal = resolved["climate"].get("rainfall_normal_mm")
    area_acres = _float(form.get("area_acres"))

    warnings = list(resolved.get("warnings") or [])

    if category == "vegetables":
        suggestions = vegetable_service.suggest(
            resolved["month"],
            temp_c=resolved["climate"].get("temp_c"),
            water_report=water_service.evaluate(
                None, source=source, days=days, rainfall_normal=rainfall_normal, fallback_need="medium"
            ),
        )
        if not suggestions:
            raise ValueError(_t(
                "No vegetable suggestion could be built for this month.",
                "या महिन्यासाठी भाजीची सूचना तयार करता आली नाही.",
            ))
        chosen = suggestions[0]
        water = water_service.evaluate(
            chosen["key"],
            source=source,
            days=days,
            rainfall_normal=rainfall_normal,
            fallback_need=chosen.get("water_need"),
            fallback_interval=chosen.get("interval_days"),
        )
        warnings.extend(water.get("warnings") or [])
        veg_calendar = _calendar_from_vegetable(chosen["key"])
        payload = {
            "mode": "vegetables",
            "resolved": resolved,
            "features": features,
            "category": category,
            "prediction": None,
            "suggestions": suggestions,
            "chosen": chosen,
            "crop": chosen["key"],
            "fertilizer": compute_vegetable_fertilizer(chosen, area_acres=area_acres),
            "market": market_for(chosen["key"], area_acres=area_acres),
            "markets": [],
            "calendar": veg_calendar,
            "water": water,
            "spray": spray_plan(chosen["key"], veg_calendar, area_acres=area_acres),
        }
    else:
        allowed = set(crops_in_category(category)) if category else None

        temp_c = resolved["climate"].get("temp_c")
        rain_mm = resolved["climate"].get("rainfall_model_mm")
        ph_value = soil.get("ph")
        suitability = suitability_service.evaluate_all(
            temp_c=temp_c, rain_mm=rain_mm, ph=ph_value, crops=sorted(allowed) if allowed else None
        )
        exclude = {crop for crop, verdict in suitability.items() if verdict["excluded"]}

        water_reports = {
            crop: water_service.evaluate(
                crop, source=source, days=days, rainfall_normal=rainfall_normal
            )
            for crop in crops_in_category(category) if allowed
        } if allowed else {}
        penalties = {
            crop: water_service.penalty_for(report) * suitability.get(crop, {}).get("score", 1.0)
            for crop, report in water_reports.items()
        }
        prediction = recommend(features, top_n=3, allowed_crops=allowed, penalties=penalties, exclude=exclude)
        water = water_reports.get(prediction["crop"]) or water_service.evaluate(
            prediction["crop"], source=source, days=days, rainfall_normal=rainfall_normal
        )
        warnings.extend(water.get("warnings") or [])
        if prediction.get("note"):
            warnings.append(prediction["note"])

        raw_rank = predict(features)
        excluded_rows = []
        for crop, _prob in raw_rank:
            if crop in exclude and (allowed is None or crop in allowed):
                verdict = suitability[crop]
                label = calendar_for(crop) or {}
                excluded_rows.append(
                    {
                        "name": label.get("label_en") or crop,
                        "name_mr": label.get("label_mr") or crop,
                        "reason": (verdict.get("reasons_short") or verdict.get("reasons") or [""])[0],
                        "reason_mr": (verdict.get("reasons_short_mr") or verdict.get("reasons_mr") or [""])[0],
                    }
                )
            if len(excluded_rows) >= 3:
                break

        top_crops = [item["crop"] for item in prediction["top"]]
        crop_calendar = calendar_for(prediction["crop"])
        payload = {
            "mode": "ml",
            "resolved": resolved,
            "features": features,
            "category": category,
            "prediction": prediction,
            "suggestions": None,
            "chosen": None,
            "crop": prediction["crop"],
            "fertilizer": compute_fertilizer(
                prediction["crop"], available, area_acres=area_acres, npk_measured=bool(available.get("measured"))
            ),
            "market": market_for(prediction["crop"], area_acres=area_acres),
            "markets": compare_markets(top_crops, area_acres=area_acres),
            "calendar": crop_calendar,
            "water": water,
            "excluded": excluded_rows,
            "spray": spray_plan(prediction["crop"], crop_calendar, area_acres=area_acres),
        }

    if category == "vegetables":
        payload["excluded"] = []

    payload["warnings"] = warnings
    payload["water_form"] = {"source": source, "days": days}
    payload["category_info"] = load_categories().get(category) if category else None
    payload["market_single"] = market_for(payload["crop"], area_acres=area_acres)
    payload["sowing"] = _preview_sowing(resolved["month"])
    payload["schedule"] = build_schedule(
        payload["crop"],
        payload["fertilizer"],
        payload["water"],
        payload["calendar"],
        payload["sowing"],
        water_source=source,
        water_days=days,
        rainfall_mm=rainfall_normal,
    )
    return payload


def _preview_sowing(month):
    """Anchor the indicative task calendar to the first of the selected month (next year if already passed)."""
    today = date.today()
    try:
        month = int(month)
    except (TypeError, ValueError):
        month = today.month
    sowing = date(today.year, month, 1)
    if sowing < today.replace(day=1):
        sowing = date(today.year + 1, month, 1)
    return sowing


def _prediction_dict(payload):
    """Shape saved to the recommendation row (ML only; vegetables store the chosen key)."""
    if payload["mode"] == "vegetables":
        chosen = payload["chosen"]
        return {
            "crop": chosen["key"],
            "confidence": None,
            "top": [
                {"crop": row["key"], "probability": None, "share": None, "name": row["name"]}
                for row in payload["suggestions"]
            ],
            "classes": len(payload["suggestions"]),
            "note": None,
            "group_filtered": True,
        }
    return payload["prediction"]


@main_bp.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template("landing.html")
    return _dashboard()


def _dashboard():
    farms = list(current_user.farms)
    pending = []
    done_count = 0
    for farm in farms:
        for task in farm.tasks:
            if task.status == "done":
                done_count += 1
            elif task.due_date:
                pending.append((task, farm))
            else:
                pending.append((task, farm))
    pending.sort(key=lambda item: (item[0].due_date or date.max))
    latest = None
    for farm in farms:
        if farm.recommendations:
            candidate = farm.recommendations[-1]
            if latest is None or (candidate.created_at or datetime.min) > (latest.created_at or datetime.min):
                latest = candidate
    stats = {
        "farms": len(farms),
        "pending": len(pending),
        "done": done_count,
        "tasks": done_count + len([1 for farm in farms for task in farm.tasks if task.status != "done"]),
        "crops": sorted({farm.crop for farm in farms if farm.crop}),
    }
    return render_template(
        "dashboard.html",
        farms=farms,
        upcoming=pending[:6],
        stats=stats,
        latest=latest,
        categories=load_categories(),
    )


def _render_recommend(status=200):
    return (
        render_template(
            "recommend.html",
            today_month=date.today().month,
            month_labels=MONTH_LABELS,
            categories=load_categories(),
            water_sources=water_service.SOURCES,
        ),
        status,
    )


@main_bp.route("/recommend", methods=["GET", "POST"])
@login_required
def recommend_view():
    if request.method == "GET":
        return _render_recommend()[0]

    try:
        resolved = _resolve_from_request()
    except ValueError as exc:
        flash(str(exc), "warning")
        return _render_recommend(400)
    except Exception as exc:
        flash(_t(f"Could not read that location: {exc}", f"ते स्थान वाचता आले नाही: {exc}"), "danger")
        return _render_recommend(502)

    try:
        payload = build_recommendation(resolved, request.form)
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("main.recommend_view"))

    return render_template(
        "result.html",
        view=payload,
        month_labels=MONTH_LABELS,
        categories=load_categories(),
        water_sources=water_service.SOURCES,
        saved=None,
    )


@main_bp.route("/farms")
@login_required
def farms():
    return render_template("farms.html", farms=current_user.farms, categories=load_categories())


@main_bp.route("/farms/<int:farm_id>")
@login_required
def farm_detail(farm_id):
    farm = db.session.get(Farm, farm_id)
    if farm is None or farm.user_id != current_user.id:
        abort(404)

    latest = farm.recommendations[-1] if farm.recommendations else None
    tasks = sorted(farm.tasks, key=lambda task: (task.due_date or date.max))
    tasks_json = [
        {
            "id": task.id,
            "date": task.due_date.isoformat() if task.due_date else None,
            "kind": task.kind,
            "title_en": task.title_en,
            "title_mr": task.title_mr,
            "detail_en": task.detail_en,
            "detail_mr": task.detail_mr,
            "status": task.status,
            "source": task.source,
        }
        for task in tasks
    ]
    notes_json = [note.as_dict() for note in farm.notes]
    water = latest.water if latest and getattr(latest, "water", None) else water_service.evaluate(
        farm.crop, source=farm.water_source, days=farm.water_days, rainfall_normal=farm.rainfall_mm
    )
    market = market_for(latest.crop, area_acres=farm.area_acres) if latest else None
    return render_template(
        "farm.html",
        farm=farm,
        latest=latest,
        tasks=tasks,
        tasks_json=json.dumps(tasks_json, ensure_ascii=False).replace("</", "<\\/"),
        notes_json=json.dumps(notes_json, ensure_ascii=False).replace("</", "<\\/"),
        water=water,
        market=market,
        month_labels=MONTH_LABELS,
        categories=load_categories(),
        today=date.today(),
    )


@main_bp.route("/farms/save", methods=["POST"])
@login_required
def save_farm():
    """Save the current prediction as a farm with its recommendation and scheduled tasks."""
    try:
        resolved = _resolve_from_request()
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("main.recommend_view"))

    try:
        payload = build_recommendation(resolved, request.form)
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("main.recommend_view"))

    npk = (
        _float(request.form.get("n")),
        _float(request.form.get("p")),
        _float(request.form.get("k")),
    )
    farm = Farm.from_resolution(
        owner=current_user,
        name=(request.form.get("farm_name") or "").strip() or _t("My Farm", "माझे शेत"),
        area_acres=_float(request.form.get("area_acres")),
        resolved=resolved,
        npk=npk if any(value is not None for value in npk) else None,
        ph=_float(request.form.get("ph")),
    )
    farm.crop_category = payload["category"] or None
    farm.water_source = payload["water_form"]["source"]
    farm.water_days = payload["water_form"]["days"]
    farm.water_note = (request.form.get("water_note") or "").strip()[:255] or None
    db.session.add(farm)
    db.session.flush()

    prediction = _prediction_dict(payload)
    farm.crop = prediction["crop"]
    recommendation = Recommendation(
        owner=current_user,
        farm=farm,
        features_json=json.dumps(payload["features"]),
        top3_json=json.dumps(prediction["top"]),
        crop=prediction["crop"],
        crop_category=payload["category"] or None,
        confidence=prediction.get("confidence"),
        fertilizer_json=json.dumps(payload["fertilizer"]),
        msp_json=json.dumps(payload["market"]),
        water_json=json.dumps(payload["water"] or {}),
        warnings_json=json.dumps(payload["warnings"]),
        spray_json=json.dumps(payload.get("spray") or {}),
        excluded_json=json.dumps(payload.get("excluded") or []),
    )
    db.session.add(recommendation)
    db.session.flush()

    sowing = request.form.get("sowing_date") or None
    tasks = list(build_tasks(farm, recommendation, sowing=sowing))
    db.session.add_all(tasks)
    db.session.commit()

    flash(
        _t(
            f"Saved '{farm.name}' with a {prediction['crop']} recommendation and {len(tasks)} scheduled tasks.",
            f"'{farm.name}' जतन केले - {prediction['crop']} शिफारस आणि {len(tasks)} कामे नियोजित.",
        ),
        "success",
    )
    return redirect(url_for("main.farm_detail", farm_id=farm.id))


@main_bp.route("/tasks/<int:task_id>/toggle", methods=["POST"])
@login_required
def toggle_task(task_id):
    task = db.session.get(FarmTask, task_id)
    if task is None or task.user_id != current_user.id:
        abort(404)
    if task.status == "done":
        task.status = "pending"
        task.completed_at = None
    else:
        task.status = "done"
        task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    return redirect(request.referrer or url_for("main.farms"))


@main_bp.route("/farms/<int:farm_id>/delete", methods=["POST"])
@login_required
def delete_farm(farm_id):
    farm = db.session.get(Farm, farm_id)
    if farm is None or farm.user_id != current_user.id:
        abort(404)
    db.session.delete(farm)
    db.session.commit()
    flash(_t(f"Deleted farm '{farm.name}'.", f"शेत '{farm.name}' हटवले."), "secondary")
    return redirect(url_for("main.farms"))


MONTH_LABELS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

MONTH_LABELS_MR = [
    "जानेवारी",
    "फेब्रुवारी",
    "मार्च",
    "एप्रिल",
    "मे",
    "जून",
    "जुलै",
    "ऑगस्ट",
    "सप्टेंबर",
    "ऑक्टोबर",
    "नोव्हेंबर",
    "डिसेंबर",
]
