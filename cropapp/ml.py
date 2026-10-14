import os
import pickle
import threading
import warnings

import pandas as pd

FEATURE_KEYS = ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]
FEATURE_LABELS = {
    "N": "Nitrogen (kg/ha)",
    "P": "Phosphorus (kg/ha)",
    "K": "Potassium (kg/ha)",
    "temperature": "Temperature (°C)",
    "humidity": "Humidity (%)",
    "ph": "pH",
    "rainfall": "Rainfall (mm)",
}
FEATURE_LABELS_MR = {
    "N": "नायट्रोजन (किग्रॅ/हेक्टर)",
    "P": "फॉस्फरस (किग्रॅ/हेक्टर)",
    "K": "पोटॅशियम (किग्रॅ/हेक्टर)",
    "temperature": "तापमान (°से)",
    "humidity": "आर्द्रता (%)",
    "ph": "pH",
    "rainfall": "पाऊस (मिमी)",
}
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model.pkl")

_lock = threading.Lock()
_model = None


def get_model():
    global _model
    with _lock:
        if _model is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with open(MODEL_PATH, "rb") as fh:
                    _model = pickle.load(fh)
    return _model


LEGACY_FEATURE_NAMES = {
    "Nitrogen": "N",
    "Phosphorus": "P",
    "Potassium": "K",
    "Temperature": "temperature",
    "Humidity": "humidity",
    "pH": "ph",
    "Rainfall": "rainfall",
}


def predict(features):
    """Return [(crop, probability), ...] sorted by probability, plus the top crop."""
    model = get_model()
    names = getattr(model, "feature_names_in_", None)
    columns = [str(name) for name in names] if names is not None else list(FEATURE_KEYS)
    keys = [LEGACY_FEATURE_NAMES.get(name, name) for name in columns]
    frame = pd.DataFrame([[float(features.get(key, 0)) for key in keys]], columns=columns)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        probabilities = model.predict_proba(frame)[0]
    classes = list(getattr(model, "classes_", []))
    ranked = sorted(zip(classes, [round(float(p), 4) for p in probabilities]), key=lambda item: item[1], reverse=True)
    return ranked


def recommend(features, top_n=3, allowed_crops=None, penalties=None, exclude=None):
    """Rank crops for the field, optionally restricted to a group and penalised for poor fit.

    allowed_crops: iterable of crop names - when given, crops outside the group are dropped
        (use for the farmer's chosen group: grains, pulses, fruits, cash crops).
    penalties: {crop: multiplier} - use for water availability fit; probabilities are kept
        raw so the displayed confidence stays comparable with the plain model output.
    exclude: iterable of crop names that cannot grow here (agro-climatic suitability fails);
        these are never recommended.
    """
    ranked = predict(features)
    classes = len(ranked)
    excluded = {crop for crop in (exclude or [])}

    selected = []
    for crop, prob in ranked:
        if crop in excluded:
            continue
        if allowed_crops is not None and crop not in allowed_crops:
            continue
        weight = 1.0
        if penalties:
            weight *= penalties.get(crop, 1.0)
        if weight > 0:
            selected.append((crop, prob * weight))

    note = None
    if not selected:
        fallback = [(crop, prob) for crop, prob in ranked if crop not in excluded]
        selected = fallback[:top_n]
        note = (
            "No crop from your chosen group fits this field's soil and climate - "
            "showing the closest crops overall."
        )
    else:
        selected.sort(key=lambda item: item[1], reverse=True)

    if not selected:
        selected = ranked[:top_n]

    group_total = sum(prob for _, prob in selected) or 1.0
    top_crop, top_prob = selected[0]
    top = []
    for crop, prob in selected[:top_n]:
        top.append(
            {
                "crop": crop,
                "probability": round(float(prob), 4),
                "share": round(float(prob) / group_total, 4),
            }
        )
    return {
        "crop": top_crop,
        "confidence": round(float(top_prob), 4),
        "group_share": top[0]["share"],
        "top": top,
        "classes": classes,
        "note": note,
        "group_filtered": allowed_crops is not None,
    }
