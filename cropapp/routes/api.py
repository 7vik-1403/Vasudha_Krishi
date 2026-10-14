import time
from types import SimpleNamespace

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user

from cropapp.extensions import db
from cropapp.models import Farm, FarmNote
from cropapp.services import ai
from cropapp.services import disease as disease_service
from cropapp.services import weather as weather_service
from utils.geo import load_districts, states_compatible
from utils.resolver import resolve, resolve_district

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _rate_limited():
    """Simple in-memory sliding-window limiter keyed by user id (or client IP)."""
    limit = int(current_app.config.get("AI_RATE_LIMIT_PER_MINUTE") or 0)
    if limit <= 0:
        return False
    bucket = current_app.extensions.setdefault("ai_rate_limit", {})
    identity = str(current_user.id) if current_user.is_authenticated else (request.remote_addr or "anon")
    now = time.monotonic()
    recent = [stamp for stamp in bucket.get(identity, []) if now - stamp < 60.0]
    if len(recent) >= limit:
        bucket[identity] = recent
        return True
    recent.append(now)
    bucket[identity] = recent
    return False


@api_bp.route("/chat", methods=["POST"])
def api_chat():
    """Gemini-backed farming assistant. Creates farm tasks and saves advice per farm."""
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400
    if len(message) > 4000:
        return jsonify({"ok": False, "error": "message is too long"}), 400
    if _rate_limited():
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "rate_limited",
                    "reply": "You are sending messages too quickly. Please wait a moment and try again.",
                }
            ),
            429,
        )

    language = session.get("language", "en")
    user = current_user if current_user.is_authenticated else SimpleNamespace(name="there", farms=[], id=None)
    history = payload.get("history") if isinstance(payload.get("history"), list) else []

    farm = None
    if current_user.is_authenticated and payload.get("farm_id") is not None:
        try:
            farm = db.session.get(Farm, int(payload.get("farm_id")))
        except (TypeError, ValueError):
            farm = None
        if farm is not None and farm.user_id != current_user.id:
            farm = None

    result = ai.ask(
        user, message, history=history, language=language, farm_id=farm.id if farm else payload.get("farm_id")
    )

    created = []
    if result.get("ok") and current_user.is_authenticated and result.get("actions"):
        created = ai.create_tasks(result["actions"], current_user)
        result["reply"] = result["reply"] + (
            f"\n\nScheduled {len(created)} task{'s' if len(created) != 1 else ''} on your farm(s): "
            + "; ".join(f"{task.title_en} (due {task.due_date})" for task in created)
            + "."
        )

    if not current_user.is_authenticated and result.get("actions"):
        result["reply"] = result["reply"] + "\n\nLog in to actually save scheduled tasks to your farm."

    note = None
    if farm is not None and result.get("reply"):
        note = FarmNote(
            user_id=current_user.id,
            farm_id=farm.id,
            question=message[:2000],
            text=result["reply"][:4000],
            source="ai",
        )
        db.session.add(note)
        db.session.commit()

    return jsonify(
        {
            "ok": bool(result.get("ok")),
            "reply": result.get("reply"),
            "error": result.get("error"),
            "language": language,
            "farm_id": farm.id if farm else None,
            "note_id": note.id if note else None,
            "tasks": [{"title": task.title_en, "due": task.due_date.isoformat(), "farm": task.farm_id} for task in created],
            "model": current_app.config.get("GEMINI_MODEL"),
        }
    )


@api_bp.route("/districts", methods=["GET"])
def api_districts():
    """District names for a state (or all states when no state is given)."""
    state = request.args.get("state")
    districts = {}
    for entry in load_districts():
        if not entry["district"]:
            continue
        if state and not states_compatible(entry["state"], state):
            continue
        districts.setdefault(entry["district"], entry["state"])
    payload = [{"district": d, "state": s} for d, s in sorted(districts.items(), key=lambda x: (x[1], x[0]))]
    return jsonify({"ok": True, "data": payload})



@api_bp.route("/resolve", methods=["GET"])
def api_resolve():
    """Resolve a map pin (lat/lon) or a district name into soil + climate with sources."""
    language = session.get("language", "en")
    try:
        if request.args.get("lat") is not None and request.args.get("lon") is not None:
            resolved = resolve(
                request.args.get("lat"), request.args.get("lon"),
                month=request.args.get("month"), language=language,
            )
        elif request.args.get("district"):
            resolved = resolve_district(
                request.args.get("district"), request.args.get("state"),
                month=request.args.get("month"), language=language,
            )
        else:
            return jsonify({"ok": False, "error": "provide lat+lon, or district (+state)"}), 400
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # network / parsing failures should not break the page
        return jsonify({"ok": False, "error": f"could not resolve this location: {exc}"}), 502
    return jsonify({"ok": True, "data": resolved})


@api_bp.route("/forecast", methods=["GET"])
def api_forecast():
    """7-day Open-Meteo forecast for a pin (lat/lon). No API key needed."""
    lat, lon = request.args.get("lat"), request.args.get("lon")
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "provide lat and lon"}), 400
    try:
        days = int(request.args.get("days", 7))
    except (TypeError, ValueError):
        days = 7
    result = weather_service.forecast(lat, lon, days=days)
    return jsonify(result), (200 if result.get("ok") else 502)


@api_bp.route("/disease", methods=["POST"])
def api_disease():
    """Leaf photo -> likely disease + treatment via Gemini vision (multipart upload)."""
    if current_app.config.get("DISEASE_DETECTION_ENABLED") is False:
        return jsonify({"ok": False, "error": "disabled"}), 404
    if _rate_limited():
        return jsonify({"ok": False, "error": "rate_limited",
                        "reply": "You are sending requests too quickly. Please wait a moment."}), 429

    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "an image file is required"}), 400

    data = upload.read()
    max_bytes = int(current_app.config.get("DISEASE_MAX_IMAGE_BYTES") or (5 * 1024 * 1024))
    if len(data) > max_bytes:
        return jsonify({"ok": False, "error": "image is too large (max 5 MB)"}), 400
    if not data:
        return jsonify({"ok": False, "error": "the uploaded file is empty"}), 400

    language = session.get("language", "en")
    crop = (request.form.get("crop") or "").strip() or None
    result = disease_service.diagnose(data, upload.mimetype, crop=crop, language=language)

    farm = None
    if current_user.is_authenticated and request.form.get("farm_id"):
        try:
            farm = db.session.get(Farm, int(request.form.get("farm_id")))
        except (TypeError, ValueError):
            farm = None
        if farm is not None and farm.user_id != current_user.id:
            farm = None

    if result.get("ok") and farm is not None and result.get("reply"):
        note = FarmNote(
            user_id=current_user.id,
            farm_id=farm.id,
            question=(f"Leaf photo diagnosis ({crop})" if crop else "Leaf photo diagnosis")[:2000],
            text=result["reply"][:4000],
            source="disease",
        )
        db.session.add(note)
        db.session.commit()
        result["note_id"] = note.id

    return jsonify(result), (200 if result.get("ok") else 502)
