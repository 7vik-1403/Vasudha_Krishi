import json
import secrets
from datetime import date, datetime, timezone

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from cropapp.extensions import db
from cropapp.ml import predict, recommend
from cropapp.models import Farm, FarmBudget, FarmRecord, FarmTask, Recommendation
from cropapp.services import analytics as analytics_service
from cropapp.services import climate_risk
from cropapp.services import economics
from cropapp.services import expenses as expense_service
from cropapp.services import history as history_service
from cropapp.services import irrigation
from cropapp.services import model_report as model_report_service
from cropapp.services import ranking
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


def _num(value, default=0.0):
    result = _float(value)
    return default if result is None else result


def _parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
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
            "ranking": [],
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
        agronomic_scores = {crop: verdict.get("score", 1.0) for crop, verdict in suitability.items()}
        prediction = recommend(
            features, top_n=3, allowed_crops=allowed, penalties=penalties,
            exclude=exclude, agronomic_scores=agronomic_scores,
        )
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
            "ranking": ranking.build(
                features, allowed=allowed, exclude=exclude, water_reports=water_reports, top_n=5
            ),
        }

    if category == "vegetables":
        payload["excluded"] = []

    irrigation_plan = irrigation.plan(
        payload["crop"],
        water=payload["water"],
        calendar=payload["calendar"],
        area_acres=area_acres,
        rainfall_normal_mm=rainfall_normal,
        source=source,
    )
    payload["irrigation"] = irrigation_plan
    payload["economics"] = economics.scenarios_for(
        payload["crop"],
        area_acres=area_acres,
        market=payload["market"],
        fertilizer=payload["fertilizer"],
        spray=payload["spray"],
        irrigation_events=irrigation_plan.get("events"),
    )
    payload["profit"] = payload["economics"].get("expected")
    payload["risks"] = climate_risk.assess(resolved, payload["crop"])

    if payload["mode"] == "ml":
        compare_crops = [item["crop"] for item in payload["prediction"]["top"]]
        contexts = {}
        for compare_crop in compare_crops:
            compare_calendar = calendar_for(compare_crop)
            compare_water = water_service.evaluate(
                compare_crop, source=source, days=days, rainfall_normal=rainfall_normal
            )
            compare_irrigation = irrigation.plan(
                compare_crop, water=compare_water, calendar=compare_calendar,
                area_acres=area_acres, rainfall_normal_mm=rainfall_normal, source=source,
            )
            contexts[compare_crop] = {
                "market": market_for(compare_crop, area_acres=area_acres),
                "fertilizer": compute_fertilizer(
                    compare_crop, available, area_acres=area_acres, npk_measured=bool(available.get("measured"))
                ),
                "spray": spray_plan(compare_crop, compare_calendar, area_acres=area_acres),
                "irrigation_events": compare_irrigation.get("events"),
            }
        payload["profit_compare"] = economics.compare_profitability(
            compare_crops, area_acres=area_acres, contexts=contexts
        )
    else:
        payload["profit_compare"] = []

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


@main_bp.route("/analytics")
@login_required
def analytics():
    data = analytics_service.build(current_user, session.get("language", "en"))
    return render_template(
        "analytics.html",
        data=data,
        analytics_json=json.dumps(data, ensure_ascii=False).replace("</", "<\\/"),
    )


@main_bp.route("/model")
def model_page():
    report = model_report_service.load()
    return render_template("model.html", report=report)


FARM_TABS = ("overview", "recommendations", "soil", "irrigation", "calendar", "expenses", "assistant")


def _farm_tasks_json(tasks):
    return json.dumps(
        [
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
        ],
        ensure_ascii=False,
    ).replace("</", "<\\/")


def _farm_soil_available(farm):
    return {
        "n": farm.soil_n,
        "p": farm.soil_p,
        "k": farm.soil_k,
        "unit": "kg/ha",
        "source": "Farm soil test",
        "measured": farm.has_npk,
    }


def _farm_recommendation_rows(farm, latest):
    """Enrich the stored top-3 crops with suitability, water need and an estimate."""
    rows = []
    top = latest.top3 or []
    crops = [row.get("crop") for row in top if row.get("crop")]
    if not crops:
        return rows
    available = _farm_soil_available(farm)
    try:
        suitability = suitability_service.evaluate_all(
            temp_c=farm.temp_c, rain_mm=farm.rainfall_mm, ph=farm.soil_ph, crops=crops
        )
    except Exception:  # pragma: no cover - defensive, never break the page
        suitability = {}
    for item in top:
        crop = item.get("crop")
        if not crop:
            continue
        verdict = suitability.get(crop) or {}
        try:
            water = water_service.evaluate(
                crop, source=farm.water_source, days=farm.water_days, rainfall_normal=farm.rainfall_mm
            )
        except Exception:  # pragma: no cover
            water = {}
        try:
            calendar = calendar_for(crop)
            estimate = economics.profitability(
                crop,
                area_acres=farm.area_acres,
                market=market_for(crop, area_acres=farm.area_acres),
                fertilizer=compute_fertilizer(crop, available, area_acres=farm.area_acres,
                                              npk_measured=bool(available["measured"])),
                spray=spray_plan(crop, calendar, area_acres=farm.area_acres),
            )
        except Exception:  # pragma: no cover
            estimate = {"available": False}
        rows.append(
            {
                "crop": crop,
                "name": calendar_for(crop).get("label_en") if calendar_for(crop) else crop,
                "probability": item.get("probability"),
                "share": item.get("share"),
                "suitability": verdict.get("score"),
                "suitability_reasons": (verdict.get("reasons_short") or [])[:3],
                "suitability_reasons_mr": (verdict.get("reasons_short_mr") or [])[:3],
                "excluded": verdict.get("excluded"),
                "water": water,
                "estimate": estimate,
            }
        )
    return rows


def _farm_workspace(farm, latest, water, market, records, history):
    """Everything the tabbed farm workspace needs, computed defensively."""
    today = date.today()
    tasks = sorted(farm.tasks, key=lambda task: (task.due_date or date.max))
    upcoming = [t for t in tasks if t.status == "pending" and (t.due_date is None or t.due_date >= today)]
    overdue = [t for t in tasks if t.status == "pending" and t.due_date and t.due_date < today]
    cancelled = [t for t in tasks if t.status == "cancelled"]
    done = [t for t in tasks if t.status == "done"]

    expense_summary = expense_service.summary(records, today=today)
    budgets = expense_service.budget_status(list(farm.budgets), expense_summary)

    missing = []
    if not farm.area_acres:
        missing.append("area")
    if farm.soil_ph is None:
        missing.append("soil_ph")
    if not farm.has_npk:
        missing.append("npk")
    if not farm.water_source:
        missing.append("water")
    if latest is None:
        missing.append("recommendation")
    total_fields = 5
    completeness = round((total_fields - len(missing)) / total_fields * 100)

    workspace = {
        "upcoming": upcoming,
        "overdue": overdue,
        "cancelled": cancelled,
        "done": done,
        "expense_summary": expense_summary,
        "budgets": budgets,
        "missing": missing,
        "completeness": completeness,
    }

    if latest:
        estimate = economics.profitability(
            latest.crop,
            area_acres=farm.area_acres,
            market=latest.msp or {},
            fertilizer=latest.fertilizer,
            spray=latest.spray,
        )
        workspace["profit"] = estimate
        workspace["reconciliation"] = expense_service.actual_vs_estimate(
            estimate, expense_summary, history.get("harvest") or {}
        )
        workspace["recommendations"] = _farm_recommendation_rows(farm, latest)
        workspace["irrigation"] = irrigation.plan(
            latest.crop,
            water=latest.water or water,
            calendar=calendar_for(latest.crop),
            area_acres=farm.area_acres,
            rainfall_normal_mm=farm.rainfall_mm,
            source=farm.water_source,
        )
        workspace["risk"] = climate_risk.assess(
            {
                "climate": {
                    "temp_c": farm.temp_c,
                    "humidity_pct": farm.humidity_pct,
                    "rainfall_normal_mm": farm.rainfall_mm,
                    "rainfall_actual_mm": None,
                    "rainfall_normal_is_state_average": False,
                }
            },
            latest.crop,
        )
    else:
        workspace["profit"] = None
        workspace["reconciliation"] = None
        workspace["recommendations"] = []
        workspace["irrigation"] = None
        workspace["risk"] = None
    return workspace


@main_bp.route("/farms/<int:farm_id>")
@login_required
def farm_detail(farm_id):
    farm = db.session.get(Farm, farm_id)
    if farm is None or farm.user_id != current_user.id:
        abort(404)

    active_tab = request.args.get("tab") or "overview"
    if active_tab not in FARM_TABS:
        active_tab = "overview"

    latest = farm.recommendations[-1] if farm.recommendations else None
    tasks = sorted(farm.tasks, key=lambda task: (task.due_date or date.max))
    notes_json = [note.as_dict() for note in farm.notes]
    water = latest.water if latest and getattr(latest, "water", None) else water_service.evaluate(
        farm.crop, source=farm.water_source, days=farm.water_days, rainfall_normal=farm.rainfall_mm
    )
    market = market_for(latest.crop, area_acres=farm.area_acres) if latest else None

    records = list(farm.records)
    history = {
        "soil": history_service.soil_series(records),
        "expenses": history_service.expense_summary(records),
        "harvest": history_service.harvest_summary(records),
    }
    history["avp"] = history_service.actual_vs_predicted(latest, history["harvest"]["latest"])

    workspace = _farm_workspace(farm, latest, water, market, records, history)

    return render_template(
        "farm.html",
        farm=farm,
        latest=latest,
        tasks=tasks,
        tasks_json=_farm_tasks_json(tasks),
        notes_json=json.dumps(notes_json, ensure_ascii=False).replace("</", "<\\/"),
        water=water,
        market=market,
        records=records,
        record_kinds=FarmRecord.KINDS,
        history=history,
        workspace=workspace,
        active_tab=active_tab,
        farm_tabs=FARM_TABS,
        expense_categories=expense_service.EXPENSE_CATEGORIES,
        payment_methods=expense_service.PAYMENT_METHODS,
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
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to save farm %r", farm.name)
        flash(
            _t(
                "Could not save this field right now. Please try again.",
                "हे शेत आत्ता जतन करता आले नाही. कृपया पुन्हा प्रयत्न करा.",
            ),
            "danger",
        )
        return redirect(url_for("main.recommend_view"))

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


@main_bp.route("/farms/<int:farm_id>/share", methods=["POST"])
@login_required
def share_farm(farm_id):
    """Create (or refresh) a read-only public link that shows this field's crop plan."""
    farm = db.session.get(Farm, farm_id)
    if farm is None or farm.user_id != current_user.id:
        abort(404)
    if not farm.share_token:
        farm.share_token = secrets.token_urlsafe(16)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Failed to create share link for farm %r", farm.name)
            flash(_t("Could not create a share link right now. Please try again.",
                     "आत्ता शेअर लिंक तयार करता आली नाही. कृपया पुन्हा प्रयत्न करा."), "danger")
            return redirect(url_for("main.farm_detail", farm_id=farm.id))
    flash(_t("Share link ready - copy it from the field header.",
             "शेअर लिंक तयार आहे - शेताच्या शीर्षलेखातून कॉपी करा."), "success")
    return redirect(url_for("main.farm_detail", farm_id=farm.id))


@main_bp.route("/farms/<int:farm_id>/unshare", methods=["POST"])
@login_required
def unshare_farm(farm_id):
    """Revoke the public link for this field."""
    farm = db.session.get(Farm, farm_id)
    if farm is None or farm.user_id != current_user.id:
        abort(404)
    farm.share_token = None
    db.session.commit()
    flash(_t("Public link revoked.", "सार्वजनिक लिंक रद्द केली."), "secondary")
    return redirect(url_for("main.farm_detail", farm_id=farm.id))


@main_bp.route("/share/<token>")
def shared_farm(token):
    """Read-only public view of a shared field plan. No private notes or records are shown."""
    farm = Farm.query.filter_by(share_token=token).first()
    if farm is None:
        abort(404)
    latest = farm.recommendations[-1] if farm.recommendations else None
    tasks = sorted(
        (task for task in farm.tasks),
        key=lambda task: (task.due_date or date.max),
    )
    market = market_for(latest.crop, area_acres=farm.area_acres) if latest else None
    water = latest.water if latest and getattr(latest, "water", None) else water_service.evaluate(
        farm.crop, source=farm.water_source, days=farm.water_days, rainfall_normal=farm.rainfall_mm
    )
    return render_template(
        "share.html",
        farm=farm,
        latest=latest,
        tasks=tasks,
        market=market,
        water=water,
        categories=load_categories(),
        today=date.today(),
    )


@main_bp.route("/farms/<int:farm_id>/records", methods=["POST"])
@login_required
def add_record(farm_id):
    """Append a dated soil / expense / harvest / irrigation / observation entry to a farm."""
    farm = db.session.get(Farm, farm_id)
    if farm is None or farm.user_id != current_user.id:
        abort(404)

    kind = (request.form.get("kind") or "").strip().lower()
    if kind not in FarmRecord.KINDS:
        flash(_t("Choose a valid record type.", "वैध नोंदीचा प्रकार निवडा."), "warning")
        return redirect(url_for("main.farm_detail", farm_id=farm.id))

    data = {}
    if kind == "soil":
        for key in ("ph", "n", "p", "k"):
            value = _float(request.form.get(key))
            if value is not None:
                data[key] = value
    elif kind == "expense":
        data["category"] = (request.form.get("category") or "other").strip().lower()[:40]
    elif kind == "harvest":
        price = _float(request.form.get("price"))
        if price is not None:
            data["price"] = price

    record = FarmRecord(
        user_id=current_user.id,
        farm_id=farm.id,
        kind=kind,
        recorded_on=_parse_date(request.form.get("recorded_on")) or date.today(),
        crop=(request.form.get("crop") or farm.crop or "").strip()[:60] or None,
        amount=_float(request.form.get("amount")),
        quantity=_float(request.form.get("quantity")),
        unit=(request.form.get("unit") or "").strip()[:20] or None,
        data_json=json.dumps(data),
        note=(request.form.get("note") or "").strip()[:1000] or None,
    )
    db.session.add(record)

    if kind == "soil":
        if "ph" in data:
            farm.soil_ph = data["ph"]
        if "n" in data:
            farm.soil_n = data["n"]
        if "p" in data:
            farm.soil_p = data["p"]
        if "k" in data:
            farm.soil_k = data["k"]

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to add %s record to farm %r", kind, farm.name)
        flash(_t("Could not save that record. Please try again.",
                 "ती नोंद जतन करता आली नाही. कृपया पुन्हा प्रयत्न करा."), "danger")
        return redirect(url_for("main.farm_detail", farm_id=farm.id))

    flash(_t("Record added to the field history.", "नोंद शेताच्या इतिहासात जोडली."), "success")
    return redirect(url_for("main.farm_detail", farm_id=farm.id))


@main_bp.route("/farms/<int:farm_id>/records/<int:record_id>/delete", methods=["POST"])
@login_required
def delete_record(farm_id, record_id):
    record = db.session.get(FarmRecord, record_id)
    if record is None or record.user_id != current_user.id or record.farm_id != farm_id:
        abort(404)
    db.session.delete(record)
    db.session.commit()
    flash(_t("Record removed.", "नोंद हटवली."), "secondary")
    return redirect(url_for("main.farm_detail", farm_id=farm_id))


TASK_KINDS = ("fertilizer", "pesticide", "spray", "irrigation", "harvest", "sowing", "other")


def _owned_farm(farm_id):
    """Fetch a farm only if it belongs to the signed-in user (ownership guard)."""
    farm = db.session.get(Farm, farm_id)
    if farm is None or farm.user_id != current_user.id:
        abort(404)
    return farm


def _tab_redirect(farm_id, tab):
    if tab not in FARM_TABS:
        tab = "overview"
    return redirect(url_for("main.farm_detail", farm_id=farm_id, tab=tab))


def _expense_error_message(errors):
    labels = {
        "category": _t("a valid category", "योग्य खर्च प्रकार"),
        "amount": _t("an amount greater than zero", "शून्यापेक्षा मोठी रक्कम"),
        "recorded_on": _t("a valid date (not in the future)", "योग्य दिनांक (भविष्यातील नाही)"),
        "quantity": _t("a non-negative quantity", "किमान शून्य परिमाण"),
        "payment_method": _t("a valid payment method", "योग्य पेमेंट पद्धत"),
    }
    wanted = ", ".join(labels.get(field, field) for field in errors)
    return _t(f"Please enter {wanted}.", f"कृपया पुरवा: {wanted}.")


@main_bp.route("/farms/<int:farm_id>/expenses", methods=["POST"])
@login_required
def add_expense(farm_id):
    """Record a categorised farm expense (kind=expense FarmRecord)."""
    farm = _owned_farm(farm_id)
    tab = request.form.get("next_tab") or "expenses"

    cleaned, errors = expense_service.validate(request.form)
    if errors:
        flash(_expense_error_message(errors), "warning")
        return _tab_redirect(farm.id, tab)
    if not cleaned.get("crop"):
        cleaned["crop"] = farm.crop or None

    record = FarmRecord(user_id=current_user.id, farm_id=farm.id)
    expense_service.apply_to_record(record, cleaned)
    db.session.add(record)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to add expense to farm %r", farm.name)
        flash(_t("Could not save that expense. Please try again.",
                 "तो खर्च जतन करता आला नाही. कृपया पुन्हा प्रयत्न करा."), "danger")
        return _tab_redirect(farm.id, tab)

    flash(_t(f"Expense of ₹{cleaned['amount']:.0f} added.",
             f"₹{cleaned['amount']:.0f} खर्च नोंदवला."), "success")
    return _tab_redirect(farm.id, tab)


@main_bp.route("/farms/<int:farm_id>/expenses/<int:expense_id>/edit", methods=["POST"])
@login_required
def edit_expense(farm_id, expense_id):
    farm = _owned_farm(farm_id)
    record = db.session.get(FarmRecord, expense_id)
    if (record is None or record.user_id != current_user.id
            or record.farm_id != farm.id or record.kind != "expense"):
        abort(404)
    tab = request.form.get("next_tab") or "expenses"

    cleaned, errors = expense_service.validate(request.form)
    if errors:
        flash(_expense_error_message(errors), "warning")
        return _tab_redirect(farm.id, tab)
    if not cleaned.get("crop"):
        cleaned["crop"] = farm.crop or None

    expense_service.apply_to_record(record, cleaned)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to edit expense %s", expense_id)
        flash(_t("Could not update that expense. Please try again.",
                 "तो खर्च अद्ययावत करता आला नाही. कृपया पुन्हा प्रयत्न करा."), "danger")
        return _tab_redirect(farm.id, tab)

    flash(_t("Expense updated.", "खर्च अद्ययावत केला."), "success")
    return _tab_redirect(farm.id, tab)


@main_bp.route("/farms/<int:farm_id>/expenses/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_expense(farm_id, expense_id):
    farm = _owned_farm(farm_id)
    record = db.session.get(FarmRecord, expense_id)
    if (record is None or record.user_id != current_user.id
            or record.farm_id != farm.id or record.kind != "expense"):
        abort(404)
    tab = request.form.get("next_tab") or "expenses"
    db.session.delete(record)
    db.session.commit()
    flash(_t("Expense removed.", "खर्च हटवला."), "secondary")
    return _tab_redirect(farm.id, tab)


@main_bp.route("/farms/<int:farm_id>/budgets", methods=["POST"])
@login_required
def set_budget(farm_id):
    """Create or update a budget for the whole farm, a crop, or a season."""
    farm = _owned_farm(farm_id)
    tab = request.form.get("next_tab") or "expenses"

    scope = (request.form.get("scope") or "farm").strip().lower()
    if scope not in FarmBudget.SCOPES:
        flash(_t("Choose a valid budget scope.", "वैध अंदाजपत्रक प्रकार निवडा."), "warning")
        return _tab_redirect(farm.id, tab)

    amount = _float(request.form.get("amount"))
    if amount is None or amount <= 0:
        flash(_t("Enter a budget amount greater than zero.",
                 "शून्यापेक्षा मोठी अंदाजपत्रक रक्कम भरा."), "warning")
        return _tab_redirect(farm.id, tab)

    crop = (request.form.get("crop") or "").strip()[:60] or None
    season = (request.form.get("season") or "").strip()[:40] or None
    if scope == "crop" and not crop:
        flash(_t("Name the crop this budget is for.", "हे अंदाजपत्रक कोणत्या पिकासाठी आहे ते लिहा."), "warning")
        return _tab_redirect(farm.id, tab)
    if scope == "season" and not season:
        flash(_t("Enter the season for this budget.", "या अंदाजपत्रकाचा हंगाम लिहा."), "warning")
        return _tab_redirect(farm.id, tab)

    warn_pct = int(_num(request.form.get("warn_pct"), 80))
    warn_pct = min(max(warn_pct, 1), 100)

    existing = None
    for budget in farm.budgets:
        if budget.scope != scope:
            continue
        if scope == "crop" and (budget.crop or None) == crop:
            existing = budget
            break
        if scope == "season" and (budget.season or None) == season:
            existing = budget
            break
        if scope == "farm":
            existing = budget
            break

    if existing:
        existing.amount = amount
        existing.warn_pct = warn_pct
    else:
        db.session.add(FarmBudget(
            user_id=current_user.id, farm_id=farm.id, scope=scope,
            crop=crop if scope == "crop" else None,
            season=season if scope == "season" else None,
            amount=amount, warn_pct=warn_pct,
        ))

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to set budget on farm %r", farm.name)
        flash(_t("Could not save the budget. Please try again.",
                 "अंदाजपत्रक जतन करता आले नाही. कृपया पुन्हा प्रयत्न करा."), "danger")
        return _tab_redirect(farm.id, tab)

    flash(_t("Budget saved.", "अंदाजपत्रक जतन केले."), "success")
    return _tab_redirect(farm.id, tab)


@main_bp.route("/farms/<int:farm_id>/budgets/<int:budget_id>/delete", methods=["POST"])
@login_required
def delete_budget(farm_id, budget_id):
    farm = _owned_farm(farm_id)
    budget = db.session.get(FarmBudget, budget_id)
    if budget is None or budget.user_id != current_user.id or budget.farm_id != farm.id:
        abort(404)
    tab = request.form.get("next_tab") or "expenses"
    db.session.delete(budget)
    db.session.commit()
    flash(_t("Budget removed.", "अंदाजपत्रक हटवले."), "secondary")
    return _tab_redirect(farm.id, tab)


@main_bp.route("/farms/<int:farm_id>/tasks", methods=["POST"])
@login_required
def add_task(farm_id):
    """Add a manual task to a farm."""
    farm = _owned_farm(farm_id)
    tab = request.form.get("next_tab") or "calendar"

    title_en = (request.form.get("title_en") or request.form.get("title") or "").strip()
    if not title_en:
        flash(_t("Give the task a title.", "कामाला शीर्षक द्या."), "warning")
        return _tab_redirect(farm.id, tab)

    kind = (request.form.get("kind") or "other").strip().lower()
    if kind not in TASK_KINDS:
        kind = "other"

    task = FarmTask(
        user_id=current_user.id,
        farm_id=farm.id,
        kind=kind,
        title_en=title_en[:255],
        title_mr=(request.form.get("title_mr") or "").strip()[:255] or None,
        detail_en=(request.form.get("detail_en") or "").strip()[:2000] or None,
        detail_mr=(request.form.get("detail_mr") or "").strip()[:2000] or None,
        due_date=_parse_date(request.form.get("due_date")),
        status="pending",
        source="manual",
    )
    db.session.add(task)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to add task to farm %r", farm.name)
        flash(_t("Could not add that task. Please try again.",
                 "ते काम जोडता आले नाही. कृपया पुन्हा प्रयत्न करा."), "danger")
        return _tab_redirect(farm.id, tab)

    flash(_t("Task added.", "काम जोडले."), "success")
    return _tab_redirect(farm.id, tab)


@main_bp.route("/tasks/<int:task_id>/edit", methods=["POST"])
@login_required
def edit_task(task_id):
    task = db.session.get(FarmTask, task_id)
    if task is None or task.user_id != current_user.id:
        abort(404)
    tab = request.form.get("next_tab") or "calendar"

    title_en = (request.form.get("title_en") or request.form.get("title") or "").strip()
    if not title_en:
        flash(_t("Give the task a title.", "कामाला शीर्षक द्या."), "warning")
        return _tab_redirect(task.farm_id, tab)

    kind = (request.form.get("kind") or task.kind or "other").strip().lower()
    if kind not in TASK_KINDS:
        kind = "other"

    task.title_en = title_en[:255]
    task.title_mr = (request.form.get("title_mr") or "").strip()[:255] or None
    task.detail_en = (request.form.get("detail_en") or "").strip()[:2000] or None
    task.detail_mr = (request.form.get("detail_mr") or "").strip()[:2000] or None
    task.kind = kind
    task.due_date = _parse_date(request.form.get("due_date"))
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to edit task %s", task_id)
        flash(_t("Could not update that task. Please try again.",
                 "ते काम अद्ययावत करता आले नाही. कृपया पुन्हा प्रयत्न करा."), "danger")
        return _tab_redirect(task.farm_id, tab)

    flash(_t("Task updated.", "काम अद्ययावत केले."), "success")
    return _tab_redirect(task.farm_id, tab)


@main_bp.route("/tasks/<int:task_id>/cancel", methods=["POST"])
@login_required
def cancel_task(task_id):
    """Cancel (or re-open) a pending task without deleting it."""
    task = db.session.get(FarmTask, task_id)
    if task is None or task.user_id != current_user.id:
        abort(404)
    tab = request.form.get("next_tab") or "calendar"
    if task.status == "cancelled":
        task.status = "pending"
        flash(_t("Task re-opened.", "काम पुन्हा सुरू केले."), "success")
    else:
        task.status = "cancelled"
        task.completed_at = None
        flash(_t("Task cancelled.", "काम रद्द केले."), "secondary")
    db.session.commit()
    return _tab_redirect(task.farm_id, tab)


@main_bp.route("/farms/<int:farm_id>/simulate", methods=["GET", "POST"])
@login_required
def simulate(farm_id):
    """What-if: re-estimate the saved crop under changed rainfall / water / price / yield / cost."""
    farm = db.session.get(Farm, farm_id)
    if farm is None or farm.user_id != current_user.id:
        abort(404)

    latest = farm.recommendations[-1] if farm.recommendations else None
    if latest is None:
        flash(_t("Save a recommendation for this field before simulating.",
                 "अनुकरण करण्यापूर्वी या शेतासाठी शिफारस जतन करा."), "warning")
        return redirect(url_for("main.farm_detail", farm_id=farm.id))

    area_acres = farm.area_acres
    features = latest.features or {}
    base_market = latest.msp or {}

    source = request.values
    rainfall = _float(source.get("rainfall_mm"))
    if rainfall is None:
        rainfall = _float(features.get("rainfall")) or 0
    water_days = _float(source.get("water_days"))
    if water_days is None:
        water_days = _float(farm.water_days)
    price_factor = _num(source.get("price_factor"), 1.0)
    yield_factor = _num(source.get("yield_factor"), 1.0)
    fertilizer_factor = _num(source.get("fertilizer_cost_factor"), 1.0)
    scenario = source.get("scenario") or "expected"
    if scenario not in economics.scenario_names():
        scenario = "expected"

    base_irrigation = irrigation.plan(
        latest.crop, water=latest.water or {}, calendar=calendar_for(latest.crop),
        area_acres=area_acres, rainfall_normal_mm=features.get("rainfall"), source=farm.water_source,
    )
    baseline = economics.profitability(
        latest.crop, area_acres=area_acres, market=base_market, fertilizer=latest.fertilizer,
        spray=latest.spray, irrigation_events=base_irrigation.get("events"), scenario=scenario,
    )

    overrides = {"fertilizer_cost_factor": fertilizer_factor}
    base_price = _float(base_market.get("price"))
    if price_factor != 1.0 and base_price:
        overrides["price"] = base_price * price_factor
    base_yield = (base_market.get("revenue") or {}).get("yield_t_ha")
    if yield_factor != 1.0 and base_yield:
        overrides["yield_t_ha"] = _num(base_yield) * yield_factor

    modified_water = water_service.evaluate(
        latest.crop, source=farm.water_source, days=water_days, rainfall_normal=rainfall
    )
    modified_irrigation = irrigation.plan(
        latest.crop, water=modified_water, calendar=calendar_for(latest.crop),
        area_acres=area_acres, rainfall_normal_mm=rainfall, source=farm.water_source,
    )
    modified = economics.profitability(
        latest.crop, area_acres=area_acres, market=base_market, fertilizer=latest.fertilizer,
        spray=latest.spray, irrigation_events=modified_irrigation.get("events"),
        scenario=scenario, overrides=overrides,
    )

    mod_features = dict(features)
    mod_features["rainfall"] = rainfall
    allowed = set(crops_in_category(farm.crop_category)) if farm.crop_category else None
    water_reports = {
        crop: water_service.evaluate(crop, source=farm.water_source, days=water_days, rainfall_normal=rainfall)
        for crop in (allowed or [])
    }
    mod_ranking = ranking.build(
        mod_features, allowed=allowed, exclude=set(), water_reports=water_reports, top_n=5
    )

    inputs = {
        "rainfall_mm": rainfall, "water_days": water_days, "price_factor": price_factor,
        "yield_factor": yield_factor, "fertilizer_cost_factor": fertilizer_factor, "scenario": scenario,
    }
    return render_template(
        "simulate.html",
        farm=farm,
        latest=latest,
        baseline=baseline,
        modified=modified,
        inputs=inputs,
        scenario_names=economics.scenario_names(),
        ranking=mod_ranking,
        modified_water=modified_water,
        month_labels=MONTH_LABELS,
    )


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
