"""Leaf-photo disease detection using Gemini vision.

Sends the uploaded leaf image inline to the configured Gemini model and asks for
a structured diagnosis (issue, confidence, organic + chemical treatment). Requires
GEMINI_API_KEY; without it the endpoint returns a clear, friendly message instead
of failing.
"""

import base64

from cropapp.services.ai import _candidate_models, _request_with_fallback

ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """You are a plant pathologist assistant for Indian farmers.
Look at the attached leaf/crop photo and diagnose the most likely disease, pest damage or nutrient deficiency.
Reply only in {language}. Answer in at most 120 words and use this exact JSON shape:
{{
  "issue": "<short name of the disease / pest / deficiency>",
  "confidence": "low" | "medium" | "high",
  "reply": "<what it likely is, the visible symptoms you used, and a numbered action plan: organic option first, then a chemical option with dose per hectare, plus one prevention tip. Concrete numbers only.>"
}}
If the image is not a plant/leaf, set issue to "Unclear image" and explain how to take a better photo.
Respond with valid JSON only, no markdown fences."""


def _lang_label(language):
    return "Marathi (Devanagari script)" if language == "mr" else "English"


def diagnose(image_bytes, mime_type, crop=None, language="en", model=None, timeout=45):
    from flask import current_app

    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME:
        return {"ok": False, "error": "unsupported image type",
                "reply": "Please upload a JPEG, PNG or WebP photo of the leaf."}

    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "ok": False,
            "error": "missing_key",
            "reply": (
                "Disease detection is switched off: set GEMINI_API_KEY in your .env file to enable it. "
                "Meanwhile use the crop calendar and spray plan on the result page."
            ),
        }

    prompt = PROMPT.format(language=_lang_label(language))
    if crop:
        prompt = f"The crop is {crop}.\n" + prompt

    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode("ascii")}},
                {"text": prompt},
            ],
        }],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 900, "responseMimeType": "application/json"},
    }

    primary = model or current_app.config.get("GEMINI_MODEL") or "gemini-flash-latest"
    candidates = _candidate_models(primary, current_app.config.get("GEMINI_FALLBACK_MODELS"))
    response, last_error = _request_with_fallback(candidates, api_key, payload, timeout, language)
    if response is None:
        return {"ok": False, "error": last_error["error"], "reply": last_error["reply"]}

    try:
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (ValueError, KeyError, IndexError):
        return {"ok": False, "error": "bad_response", "reply": "The AI service returned an unreadable reply."}

    parsed = _parse(text)
    reply = parsed.get("reply") or text.strip()[:1500]
    return {
        "ok": True,
        "issue": parsed.get("issue"),
        "confidence": parsed.get("confidence"),
        "reply": reply,
        "language": language,
    }


def _parse(text):
    import json

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}
