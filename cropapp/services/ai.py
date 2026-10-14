import json
import time
from datetime import date, datetime

import requests

from cropapp.models import FarmTask
from cropapp.services import climate_risk, economics, history

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ALLOWED_KINDS = {"fertilizer", "pesticide", "irrigation", "harvest", "other"}

RETRY_STATUS = {429}
SERVER_BUSY_STATUS = {500, 502, 503, 504}
AUTH_STATUS = {401, 403}
DEFAULT_FALLBACK_MODELS = (
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
)
MAX_ATTEMPTS_PER_MODEL = 2
BACKOFF_SECONDS = 2.0

SYSTEM_TEMPLATE = """You are the Crop Advisor assistant inside an Indian crop-recommendation web app used by farmers.
Reply ONLY in {language}. Keep answers short (max 120 words), practical and farmer-friendly.
Always give concrete numbers for any dose (kg per hectare, kg per acre, ml per hectare, grams per hectare, days).
Never invent soil or weather readings that are not in the context; if something is missing, ask the farmer to enter it.
You may suggest local alternatives (KVK, Soil Health Card, nearest mandi) but never claim to be a licensed agronomist.

Context you can rely on:
{context}

If the farmer asks you to schedule / add / remember a task (fertilizer application, pesticide or fungicide spray,
irrigation, sowing or harvest), you must answer with this exact JSON shape and nothing else:
{{
  "reply": "<your normal answer to the farmer>",
  "actions": [
    {{
      "type": "add_task",
      "farm_id": <one of the farm ids from the context>,
      "kind": "fertilizer" | "pesticide" | "irrigation" | "harvest" | "other",
      "title_en": "<short title in English>",
      "title_mr": "<short title in Marathi (Devanagari)>",
      "detail_en": "<dose and instructions in English, with numbers and units>",
      "detail_mr": "<dose and instructions in Marathi>",
      "due_date": "YYYY-MM-DD"
    }}
  ]
}}
Rules for actions: only use farm_id values listed in the context, use today's date {today} as the reference for
due dates, keep at most 3 actions per reply, and only schedule what the farmer actually asked for.
If no task should be scheduled, reply with "actions": [].
Always respond with valid JSON only (no markdown fences)."""


def has_key():
    from flask import current_app

    return bool(current_app.config.get("GEMINI_API_KEY"))


def build_context(user, farm_id=None, today=None):
    today = today or date.today()
    lines = [f"Today: {today.isoformat()}", f"Farmer name: {getattr(user, 'name', 'there')}"]
    farms = list(getattr(user, "farms", []) or [])
    if not farms:
        lines.append("No saved farms yet. Ask the farmer to pick a location on the map and save a field first.")
    for farm in farms:
        marker = " (selected)" if farm_id and farm.id == farm_id else ""
        lines.append(
            f"Farm id {farm.id}{marker}: '{farm.name}', {farm.district or 'unknown district'}, "
            f"{farm.state or ''}, area {farm.area_acres or 'unknown'} acres, current crop {farm.crop or 'not set'}."
        )
        if farm.soil_ph is not None:
            lines.append(
                f"  Soil: pH {farm.soil_ph} ({farm.ph_source or 'source unknown'}), "
                f"N {farm.soil_n}, P {farm.soil_p}, K {farm.soil_k} kg/ha"
                + (f", texture {farm.texture_class}" if farm.texture_class else "")
                + (f", organic C {farm.organic_carbon} g/kg" if farm.organic_carbon else "")
                + "."
            )
        if farm.temp_c is not None:
            lines.append(
                f"  Climate: {farm.temp_c} C, {farm.humidity_pct}% humidity, "
                f"monthly rainfall {farm.rainfall_mm} mm."
            )
        pending = [task for task in farm.tasks if task.status == "pending"]
        pending.sort(key=lambda task: (task.due_date or date.max))
        for task in pending[:5]:
            lines.append(f"  Upcoming task: due {task.due_date} - {task.title_en}.")
        records = list(getattr(farm, "records", []) or [])
        if records:
            latest_record = records[0]
            lines.append(
                f"  Latest field record: {latest_record.kind} on {latest_record.recorded_on}"
                + (f" ({latest_record.note})" if latest_record.note else "") + "."
            )
            expenses = history.expense_summary(records)
            if expenses["total"]:
                lines.append(f"  Total recorded expenses so far: Rs {expenses['total']}.")
            harvest = history.harvest_summary(records)
            if harvest["count"]:
                lines.append(
                    f"  Recorded harvests: {harvest['count']} totalling {harvest['total_quantity']} kg / "
                    f"Rs {harvest['total_revenue']}."
                )

    latest = None
    for farm in farms:
        if farm.recommendations:
            candidate = farm.recommendations[-1]
            if latest is None or candidate.created_at > latest.created_at:
                latest = candidate
    if latest is not None:
        fert = latest.fertilizer or {}
        products = fert.get("products") or {}
        lines.append(
            f"Latest recommendation: {latest.crop} ({round((latest.confidence or 0) * 100)}% confidence). "
            f"Suggested dose: urea {products.get('urea_kg_ha')} kg/ha, DAP {products.get('dap_kg_ha')} kg/ha, "
            f"MOP {products.get('mop_kg_ha')} kg/ha."
        )
        cost = fert.get("cost") or {}
        for option in cost.get("options") or []:
            line = (
                f"  Fertilizer cost - {option.get('label_en')}: about Rs {option.get('cost_per_acre')}/acre"
                f" (Rs {option.get('cost_per_ha')}/ha"
            )
            if option.get("total"):
                line += f", Rs {option['total']} for the whole field"
            lines.append(line + ").")
        savings = cost.get("savings") or {}
        if savings.get("per_acre"):
            lines.append(
                f"  Cheaper fertilizer plan saves about Rs {savings.get('per_acre')}/acre ({savings.get('pct')}%)."
            )

        spray = latest.spray or {}
        for row in spray.get("rows") or []:
            primary = row.get("primary") or {}
            alt = row.get("alt") or {}
            if not primary:
                continue
            segment = f"  Spray for {row.get('name_en')}: {primary.get('name_en')}"
            if primary.get("cost_per_acre") is not None:
                segment += f" ~Rs {primary.get('cost_per_acre')}/acre"
            if alt:
                label = "cheaper alternative" if row.get("alt_cheaper") else "alternative"
                segment += f"; {label} {alt.get('name_en')}"
                if alt.get("cost_per_acre") is not None:
                    segment += f" ~Rs {alt.get('cost_per_acre')}/acre"
            lines.append(segment + ".")
        totals = spray.get("totals") or {}
        if totals.get("per_acre"):
            lines.append(
                f"  Total spray cost about Rs {totals.get('per_acre')}/acre per season "
                f"({spray.get('applications')} applications per pest window)."
            )

        excluded = latest.excluded or []
        if excluded:
            lines.append(
                "  Crops ruled out by climate/soil here: "
                + "; ".join(f"{item.get('name')} ({item.get('reason')})" for item in excluded)
                + "."
            )
        if latest.warnings:
            lines.append("  Warnings: " + " ".join(latest.warnings))

        farm = latest.farm
        profit = economics.profitability(
            latest.crop,
            area_acres=farm.area_acres if farm else None,
            market=latest.msp or {}, fertilizer=latest.fertilizer, spray=latest.spray,
            scenario="expected",
        )
        if profit.get("available"):
            lines.append(
                f"  Profit estimate (indicative, expected case): net about Rs {profit['net']['per_acre']}/acre, "
                f"costs about Rs {profit['costs']['per_acre']}/acre, margin {profit['margin_pct']}%."
            )
        if farm and farm.temp_c is not None:
            risk = climate_risk.assess(
                {"climate": {
                    "temp_c": farm.temp_c, "humidity_pct": farm.humidity_pct,
                    "rainfall_normal_mm": farm.rainfall_mm, "rainfall_actual_mm": None,
                    "rainfall_normal_is_state_average": False,
                }},
                latest.crop,
            )
            if risk["risks"]:
                lines.append("  Climate risks: " + "; ".join(item["title_en"] for item in risk["risks"][:3]) + ".")
    return "\n".join(lines)


def _language_label(language):
    return "Marathi (Devanagari script)" if language == "mr" else "English"


def _candidate_models(primary, extra=""):
    """Primary model first, then built-in fallbacks, then any admin-configured ones."""
    models = []
    for name in [primary, *DEFAULT_FALLBACK_MODELS]:
        name = (name or "").strip()
        if name and name not in models:
            models.append(name)
    for name in str(extra or "").split(","):
        name = name.strip()
        if name and name not in models:
            models.append(name)
    return models


def _busy_reply(language):
    if language == "mr":
        return "AI सहाय्यक आत्ता व्यस्त आहे (तात्पुरती मागणी). कृपया थोड्या वेळाने पुन्हा प्रयत्न करा."
    return (
        "The AI assistant is busy right now (temporary demand spike). "
        "Please try again in a moment - the crop plan below still works without it."
    )


def _request_with_fallback(candidates, api_key, payload, timeout, language):
    """Post to Gemini, retrying transient errors and falling through to backup models."""
    last_error = {"error": "unknown", "reply": "The AI service did not respond."}
    for name in candidates:
        for attempt in range(MAX_ATTEMPTS_PER_MODEL):
            try:
                response = requests.post(
                    GEMINI_ENDPOINT.format(model=name),
                    params={"key": api_key},
                    headers={"x-goog-api-key": api_key},
                    json=payload,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                last_error = {"error": "network", "reply": f"Could not reach the AI service ({exc})."}
                time.sleep(BACKOFF_SECONDS * (attempt + 1))
                continue
            if response.status_code == 200:
                return response, last_error
            detail = response.text[:300]
            if response.status_code in AUTH_STATUS:
                return None, {
                    "error": f"http_{response.status_code}",
                    "reply": f"The AI service returned an error ({response.status_code}): {detail}",
                }
            if response.status_code in RETRY_STATUS:
                last_error = {"error": f"http_{response.status_code}", "reply": _busy_reply(language)}
                time.sleep(BACKOFF_SECONDS * (attempt + 1))
                continue
            if response.status_code in SERVER_BUSY_STATUS:
                last_error = {"error": f"http_{response.status_code}", "reply": _busy_reply(language)}
                break
            last_error = {
                "error": f"http_{response.status_code}",
                "reply": f"The AI service returned an error ({response.status_code}): {detail}",
            }
            break
    return None, last_error


def ask(user, message, history=None, language="en", farm_id=None, model=None, timeout=45):
    """Call Gemini and return {"reply", "actions", "ok", "error"} with validated task actions."""
    from flask import current_app

    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "ok": False,
            "error": "missing_key",
            "reply": (
                "The AI assistant is switched off: set GEMINI_API_KEY in a .env file to enable it. "
                "Meanwhile the crop calendar and fertilizer doses on the result page are generated locally."
            ),
            "actions": [],
        }

    language_label = _language_label(language)
    today = date.today().isoformat()
    system = SYSTEM_TEMPLATE.format(language=language_label, context=build_context(user, farm_id, date.today()), today=today)

    contents = []
    for item in (history or [])[-10:]:
        role = "model" if item.get("role") == "assistant" else "user"
        text = str(item.get("text") or item.get("message") or "")[:2000]
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": str(message)[:4000]}]})

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.45, "maxOutputTokens": 1024, "responseMimeType": "application/json"},
    }
    primary = model or current_app.config.get("GEMINI_MODEL") or "gemini-3.8-flash"
    candidates = _candidate_models(primary, current_app.config.get("GEMINI_FALLBACK_MODELS"))
    response, last_error = _request_with_fallback(candidates, api_key, payload, timeout, language)
    if response is None:
        return {"ok": False, "error": last_error["error"], "reply": last_error["reply"], "actions": []}

    try:
        body = response.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (ValueError, KeyError, IndexError):
        return {"ok": False, "error": "bad_response", "reply": "The AI service returned an unreadable reply.", "actions": []}

    parsed = _parse(text)
    reply = parsed.get("reply") or parsed.get("reply_mr") or ""
    if not reply:
        reply = text.strip()[:1500]
    actions = [action for action in parsed.get("actions") or [] if isinstance(action, dict)]
    return {"ok": True, "reply": reply, "actions": actions, "language": language}


def _parse(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if cleaned.count("```") > 1 else cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def validate_actions(actions, user, farm_ids=None):
    """Keep only well-formed actions that target the caller's own farms."""
    allowed_ids = {farm.id for farm in user.farms}
    today = date.today()
    valid = []
    for action in actions[:3]:
        try:
            farm_id = int(action.get("farm_id"))
        except (TypeError, ValueError):
            continue
        if farm_id not in allowed_ids:
            continue
        kind = str(action.get("kind") or "other").lower()
        if kind not in ALLOWED_KINDS:
            kind = "other"
        title_en = str(action.get("title_en") or action.get("title") or "").strip()[:255]
        title_mr = str(action.get("title_mr") or "").strip()[:255]
        detail_en = str(action.get("detail_en") or action.get("detail") or "").strip()[:2000]
        detail_mr = str(action.get("detail_mr") or "").strip()[:2000]
        if not title_en and not title_mr:
            continue
        if not title_en:
            title_en = title_mr
        due = _parse_date(action.get("due_date")) or today
        valid.append(
            dict(
                user_id=user.id,
                farm_id=farm_id,
                kind=kind,
                title_en=title_en,
                title_mr=title_mr or title_en,
                detail_en=detail_en,
                detail_mr=detail_mr or detail_en,
                due_date=due,
                source="ai",
            )
        )
    return valid


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def create_tasks(actions, user):
    if not actions:
        return []
    tasks = [FarmTask(**payload) for payload in validate_actions(actions, user)]
    if tasks:
        from cropapp.extensions import db

        db.session.add_all(tasks)
        db.session.commit()
    return tasks
