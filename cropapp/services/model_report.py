"""Load and decorate the training report (model_report.json) for the model page."""

import json
import os

from cropapp.ml import FEATURE_KEYS, FEATURE_LABELS, FEATURE_LABELS_MR, MODEL_PATH

REPORT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "model_report.json")

_DATASET_CANDIDATES = (
    os.path.join(os.path.dirname(REPORT_PATH), "Crop_recommendation.csv"),
    os.path.join(os.path.dirname(REPORT_PATH), "data", "Crop_recommendation.csv"),
)


def dataset_path():
    for path in _DATASET_CANDIDATES:
        if os.path.exists(path):
            return os.path.relpath(path, os.path.dirname(REPORT_PATH))
    return None


def model_exists():
    return os.path.exists(MODEL_PATH)


def load():
    """Return the training report plus labels and file metadata. Never raises."""
    try:
        with open(REPORT_PATH, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        return {"available": False, "features": []}

    features = report.get("features") or list(FEATURE_KEYS)
    label_map = [
        {"key": key, "label_en": FEATURE_LABELS.get(key, key), "label_mr": FEATURE_LABELS_MR.get(key, key)}
        for key in features
    ]
    report["available"] = True
    report["feature_labels"] = label_map
    report["dataset_path"] = dataset_path()
    report["model_present"] = model_exists()
    report["candidates_sorted"] = sorted(
        report.get("candidates") or [],
        key=lambda item: item.get("cv_accuracy_mean") or 0,
        reverse=True,
    )
    report["confusion_labels"] = report.get("confusion_matrix_labels") or report.get("classes") or []
    return report
