from types import SimpleNamespace

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user

from cropapp.extensions import db
from cropapp.models import Farm, FarmNote
from cropapp.services import ai
from utils.geo import load_districts, states_compatible
from utils.resolver import resolve, resolve_district

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/chat", methods=["POST"])
def api_chat():
    """Gemini-backed farming assistant. Creates farm tasks and saves advice per farm."""
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400
    if len(message) > 4000:
        return jsonify({"ok": False, "error": "message is too long"}), 400

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
