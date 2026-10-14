"""Explainable crop ranking.

Separates two different things that are easy to confuse:

* the ML model score - an uncalibrated classifier probability, and
* the agronomic suitability score - a rule-based fit to the crop's temperature,
  rainfall and soil-pH windows.

The combined score is simply `model_probability x suitability_score x water_penalty`
and every factor is shown so the ranking can be explained, not just asserted.
"""

from cropapp.ml import predict
from cropapp.services import suitability as suitability_service
from cropapp.services import water as water_service
from cropapp.services.datasets import calendar_for

MODEL_NOTE_EN = "Model score is an uncalibrated classifier probability, not a guarantee."
MODEL_NOTE_MR = "मॉडेल स्कोअर ही कॅलिब्रेट न केलेली वर्गीकरण संभाव्यता आहे, हमी नाही."


def _label(crop):
    calendar = calendar_for(crop) or {}
    return calendar.get("label_en") or crop, calendar.get("label_mr") or crop


def explain(crop, features, verdict, water_report):
    features = features or {}
    limits = (verdict or {}).get("limits") or {}
    factors_en, factors_mr = [], []
    limitations_en, limitations_mr = [], []
    risks_en, risks_mr = [], []
    missing_en, missing_mr = [], []

    temp = features.get("temperature")
    rain = features.get("rainfall")
    ph = features.get("ph")

    tmin, tmax = limits.get("temp_min"), limits.get("temp_max")
    if tmin is not None and tmax is not None and temp:
        if tmin <= temp <= tmax:
            factors_en.append(f"Temperature {temp}°C sits inside the {tmin}-{tmax}°C window.")
            factors_mr.append(f"तापमान {temp}°C हे {tmin}-{tmax}°C मर्यादेत आहे.")
    rmin, rmax = limits.get("rain_min"), limits.get("rain_max")
    if rmin is not None and rmax is not None and rain:
        if rmin <= rain <= rmax:
            factors_en.append(f"Rainfall {rain} mm sits inside the {rmin}-{rmax} mm window.")
            factors_mr.append(f"पाऊस {rain} मिमी हा {rmin}-{rmax} मिमी मर्यादेत आहे.")
    pmin, pmax = limits.get("ph_min"), limits.get("ph_max")
    if pmin is not None and pmax is not None and ph:
        if pmin <= ph <= pmax:
            factors_en.append(f"Soil pH {ph} is in the {pmin}-{pmax} range this crop likes.")
            factors_mr.append(f"मातीचा सामू {ph} हा {pmin}-{pmax} मर्यादेत आहे.")

    if not limits:
        missing_en.append("No crop-specific climate window on file, so climate fit is not scored in detail.")
        missing_mr.append("या पिकासाठी विशिष्ट हवामान मर्यादा नाही, त्यामुळे हवामान जुळणी सविस्तर मोजली जात नाही.")

    # Suitability soft/hard reasons read as limitations.
    limitations_en.extend((verdict or {}).get("reasons") or [])
    limitations_mr.extend((verdict or {}).get("reasons_mr") or [])

    if water_report and water_report.get("known"):
        source = water_report.get("source_label") or water_report.get("source") or "your source"
        if water_report.get("sufficient"):
            factors_en.append(f"Your {source} supply covers this crop's water need.")
            factors_mr.append(f"तुमचा {source} पुरवठा या पिकाची पाण्याची गरज भागवतो.")
        else:
            risks_en.append(f"Water is short for this crop with {source} - the ranking is reduced accordingly.")
            risks_mr.append(f"{source} सह या पिकासाठी पाणी कमी पडते - त्यानुसार क्रमांक कमी केला आहे.")

    for key, value, name_en, name_mr in (
        ("temperature", temp, "Temperature", "तापमान"),
        ("rainfall", rain, "Rainfall", "पाऊस"),
        ("ph", ph, "Soil pH", "मातीचा सामू"),
    ):
        if not value:
            missing_en.append(f"{name_en} reading is missing or zero.")
            missing_mr.append(f"{name_mr} माहिती नाही किंवा शून्य आहे.")

    return {
        "factors_en": factors_en[:4], "factors_mr": factors_mr[:4],
        "limitations_en": limitations_en[:3], "limitations_mr": limitations_mr[:3],
        "risks_en": risks_en[:3], "risks_mr": risks_mr[:3],
        "missing_en": missing_en[:3], "missing_mr": missing_mr[:3],
        "model_note_en": MODEL_NOTE_EN, "model_note_mr": MODEL_NOTE_MR,
    }


def build(features, allowed=None, exclude=None, water_reports=None, top_n=5):
    """Rank crops by combined score and attach an explanation to each."""
    features = features or {}
    allowed = set(allowed) if allowed else None
    exclude = set(exclude or ())
    water_reports = water_reports or {}

    gathered = []
    for crop, probability in predict(features):
        if allowed is not None and crop not in allowed:
            continue
        if crop in exclude:
            continue
        verdict = suitability_service.evaluate(
            crop, temp_c=features.get("temperature"), rain_mm=features.get("rainfall"), ph=features.get("ph")
        )
        report = water_reports.get(crop)
        penalty = water_service.penalty_for(report) if report else 1.0
        gathered.append({
            "crop": crop, "probability": float(probability),
            "agronomic": verdict["score"], "penalty": penalty,
            "verdict": verdict, "report": report,
        })

    # A one-hot classifier can return ~0 for every crop once its top pick is excluded
    # (an artefact of the small synthetic training set). Fall back to the agronomic x
    # water fit renormalised into a probability-like score so the ranking stays meaningful.
    degenerate = bool(gathered) and max(item["probability"] for item in gathered) < 0.01
    if degenerate:
        total = sum(item["agronomic"] * item["penalty"] for item in gathered) or 1.0
        for item in gathered:
            item["probability"] = (item["agronomic"] * item["penalty"]) / total

    rows = []
    for item in gathered:
        name_en, name_mr = _label(item["crop"])
        probability = round(item["probability"], 4)
        rows.append({
            "crop": item["crop"], "name_en": name_en, "name_mr": name_mr,
            "model_probability": probability,
            "model_degenerate": degenerate,
            "agronomic_score": item["agronomic"],
            "water_penalty": item["penalty"],
            "combined_score": round(probability * item["agronomic"] * item["penalty"], 4),
            "explanation": explain(item["crop"], features, item["verdict"], item["report"]),
        })

    rows.sort(key=lambda row: row["combined_score"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows[:top_n]
