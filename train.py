"""Retrain and compare crop recommendation models.

Usage:
    python train.py                     # evaluate all candidates, save the best (calibrated)
    python train.py --model rf          # force a specific candidate (gnb|dt|rf|hgb|svc|knn|logreg)
    python train.py --no-calibrate      # ship the raw estimator without probability calibration
    python train.py --calibration isotonic

The saved model is wrapped in a probability calibrator by default because the
public Crop Recommendation dataset makes several classifiers (notably Gaussian
Naive Bayes) wildly over-confident: a single crop gets probability ~1.0 and all
others ~0.0. Calibration keeps the ranking but restores usable confidence values
for the UI. The training report records the before/after metrics so the change
is auditable.
"""

import argparse
import json
import os
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, label_binarize
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "Crop_recommendation.csv")
MODEL_PATH = os.path.join(BASE_DIR, "model.pkl")
REPORT_PATH = os.path.join(BASE_DIR, "model_report.json")

FEATURE_KEYS = ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]
TARGET = "label"
SEED = 42

CANDIDATES = {
    "gnb": ("Gaussian Naive Bayes", GaussianNB()),
    "dt": ("Decision Tree", DecisionTreeClassifier(random_state=SEED, max_depth=None)),
    "rf": (
        "Random Forest",
        RandomForestClassifier(n_estimators=400, random_state=SEED, n_jobs=1, class_weight="balanced_subsample"),
    ),
    "hgb": (
        "Histogram Gradient Boosting",
        HistGradientBoostingClassifier(max_iter=300, learning_rate=0.1, random_state=SEED),
    ),
    "svc": ("RBF SVM", Pipeline([("scaler", StandardScaler()), ("svc", SVC(C=10, kernel="rbf", probability=True, random_state=SEED))])),
    "knn": ("K-Nearest Neighbours", Pipeline([("scaler", StandardScaler()), ("knn", KNeighborsClassifier(n_neighbors=5))])),
    "logreg": (
        "Multinomial Logistic Regression",
        Pipeline([("scaler", StandardScaler()), ("logreg", LogisticRegression(max_iter=2000, random_state=SEED))]),
    ),
}


def load_data():
    frame = pd.read_csv(DATA_PATH)
    missing = [key for key in FEATURE_KEYS if key not in frame.columns]
    if missing:
        raise SystemExit(f"Dataset is missing columns: {missing}")
    if TARGET not in frame.columns:
        raise SystemExit(f"Dataset is missing the '{TARGET}' column")

    before = len(frame)
    frame = frame.drop_duplicates(subset=FEATURE_KEYS + [TARGET]).reset_index(drop=True)
    dropped = before - len(frame)
    if dropped:
        print(f"Dropped {dropped} exact duplicate rows to avoid train/test leakage.")
    return frame


def top_k_accuracy(y_true, probabilities, classes, k=3):
    """Fraction of rows whose true label is among the k highest-probability classes."""
    order = np.argsort(-probabilities, axis=1)[:, :k]
    top = [[classes[index] for index in row] for row in order]
    return float(np.mean([actual in predicted for actual, predicted in zip(y_true, top)]))


def evaluate(frame, only=None):
    x = frame[FEATURE_KEYS]
    y = frame[TARGET]

    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=0.2, random_state=SEED, stratify=y
    )

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    results = []
    for key, (label, model) in CANDIDATES.items():
        if only and key != only:
            continue
        scores = cross_val_score(model, x_train, y_train, cv=cv, scoring="accuracy", n_jobs=1)
        model.fit(x_train, y_train)
        predictions = model.predict(x_test)
        entry = {
            "id": key,
            "label": label,
            "cv_accuracy_mean": round(float(scores.mean()), 4),
            "cv_accuracy_std": round(float(scores.std()), 4),
            "holdout_accuracy": round(float(accuracy_score(y_test, predictions)), 4),
            "holdout_macro_f1": round(float(f1_score(y_test, predictions, average="macro")), 4),
            "holdout_macro_precision": round(float(precision_score(y_test, predictions, average="macro", zero_division=0)), 4),
            "holdout_macro_recall": round(float(recall_score(y_test, predictions, average="macro", zero_division=0)), 4),
            "holdout_balanced_accuracy": round(float(balanced_accuracy_score(y_test, predictions)), 4),
        }
        if hasattr(model, "predict_proba"):
            proba = model.predict_proba(x_test)
            entry["holdout_top3_accuracy"] = round(top_k_accuracy(list(y_test), proba, list(model.classes_), k=3), 4)
        results.append(entry)
        print(
            f"{label:<36} CV {entry['cv_accuracy_mean']:.4f} +/- {entry['cv_accuracy_std']:.4f} "
            f"| holdout acc {entry['holdout_accuracy']:.4f} | macro-F1 {entry['holdout_macro_f1']:.4f} "
            f"| top-3 {entry.get('holdout_top3_accuracy', float('nan')):.4f}"
        )

    # Prefer balanced macro-F1 (robust to class imbalance), then accuracy, then top-3.
    results.sort(
        key=lambda item: (
            item["holdout_macro_f1"],
            item["holdout_accuracy"],
            item.get("holdout_top3_accuracy", 0.0),
        ),
        reverse=True,
    )
    return results, (x_train, x_test, y_train, y_test)


def _probability_metrics(y_test, probabilities, classes):
    """Mean multiclass log-loss and Brier score (both lower is better)."""
    y_true = np.asarray(y_test)
    y_binary = label_binarize(y_true, classes=list(classes))
    logloss = float(log_loss(y_true, probabilities, labels=list(classes)))
    brier = float(np.mean(np.sum((y_binary - probabilities) ** 2, axis=1)))
    return logloss, brier


def _feature_importance(model):
    """Best-effort feature importance, unwrapping pipelines and calibrators."""
    estimator = model
    if isinstance(estimator, CalibratedClassifierCV):
        members = getattr(estimator, "calibrated_classifiers_", []) or []
        stacked = []
        for member in members:
            inner = getattr(member, "estimator", member)
            values = _feature_importance(inner)
            if values:
                stacked.append(values)
        if not stacked:
            return None
        keys = stacked[0].keys()
        return {key: round(sum(row[key] for row in stacked) / len(stacked), 4) for key in keys}
    if isinstance(estimator, Pipeline):
        estimator = estimator.steps[-1][1]
    values = getattr(estimator, "feature_importances_", None)
    if values is None:
        return None
    return {key: round(float(value), 4) for key, value in zip(FEATURE_KEYS, values)}


def main():
    parser = argparse.ArgumentParser(description="Train the crop recommendation model")
    parser.add_argument("--model", choices=sorted(CANDIDATES), default=None, help="force one candidate")
    parser.add_argument(
        "--calibration",
        choices=("sigmoid", "isotonic"),
        default="sigmoid",
        help="probability calibration method applied to the shipped model",
    )
    parser.add_argument("--no-calibrate", action="store_true", help="ship the raw estimator (no calibration)")
    args = parser.parse_args()

    warnings.filterwarnings("ignore")
    frame = load_data()
    print(f"Dataset: {len(frame)} rows, {frame[TARGET].nunique()} crops, features {FEATURE_KEYS}")

    results, (x_train, x_test, y_train, y_test) = evaluate(frame, only=args.model)
    best = results[0]
    print(f"\nBest model: {best['label']} (holdout accuracy {best['holdout_accuracy']:.4f})")

    base_model = CANDIDATES[best["id"]][1]
    base_model.fit(x_train, y_train)
    base_predictions = base_model.predict(x_test)
    report = classification_report(y_test, base_predictions, output_dict=True, zero_division=0)
    labels = sorted(frame[TARGET].unique())
    matrix = confusion_matrix(y_test, base_predictions, labels=labels)

    classes = list(base_model.classes_)
    base_proba = base_model.predict_proba(x_test)
    base_logloss, base_brier = _probability_metrics(y_test, base_proba, classes)

    calibration = None
    shipped = base_model
    if not args.no_calibrate:
        calibrator = CalibratedClassifierCV(base_model, method=args.calibration, cv=5)
        calibrator.fit(x_train, y_train)
        calibrated_proba = calibrator.predict_proba(x_test)
        calibrated_predictions = calibrator.predict(x_test)
        cal_logloss, cal_brier = _probability_metrics(y_test, calibrated_proba, classes)
        # Only ship the calibrator when it genuinely improves probability quality.
        # On this well-separated synthetic dataset the raw GaussianNB is already
        # near-perfectly ranked, so calibration can make log-loss worse; keep raw then.
        applied = cal_logloss < base_logloss and cal_brier <= base_brier
        calibration = {
            "method": args.calibration,
            "cv": 5,
            "applied": applied,
            "holdout_accuracy": round(float(accuracy_score(y_test, calibrated_predictions)), 4),
            "holdout_macro_f1": round(float(f1_score(y_test, calibrated_predictions, average="macro")), 4),
            "log_loss_before": round(base_logloss, 4),
            "log_loss_after": round(cal_logloss, 4),
            "brier_before": round(base_brier, 4),
            "brier_after": round(cal_brier, 4),
            "reason": (
                "Calibration improved probability quality (lower log-loss and Brier); shipped."
                if applied
                else "Calibration did not improve log-loss/Brier on this dataset; the raw estimator was kept."
            ),
        }
        if applied:
            shipped = calibrator
        print(
            f"Calibration ({args.calibration}): log-loss {base_logloss:.4f} -> {cal_logloss:.4f}, "
            f"Brier {base_brier:.4f} -> {cal_brier:.4f} -> {'applied' if applied else 'not applied'}"
        )

    with open(MODEL_PATH, "wb") as fh:
        pickle_dump(shipped, fh)
    print(f"Saved model -> {MODEL_PATH}")

    payload = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sklearn_version": __import__("sklearn").__version__,
        "dataset_rows": int(len(frame)),
        "classes": labels,
        "features": FEATURE_KEYS,
        "candidates": results,
        "best": best,
        "shipped": {
            "id": best["id"],
            "label": best["label"],
            "calibrated": bool(calibration and calibration["applied"]),
            "calibration_method": calibration["method"] if calibration else None,
        },
        "calibration": calibration,
        "holdout_accuracy": best["holdout_accuracy"],
        "holdout_macro_f1": best["holdout_macro_f1"],
        "classification_report": report,
        "confusion_matrix": matrix.tolist(),
        "confusion_matrix_labels": labels,
        "feature_importance": _feature_importance(shipped),
        "caveat": (
            "The public Crop Recommendation dataset has narrow, well-separated per-crop feature ranges, "
            "so random-split scores are optimistically high compared to real fields. "
            "Treat them as a relative ranking of models, not a field accuracy claim."
        ),
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print(f"Saved training report -> {REPORT_PATH}")

    if payload["feature_importance"]:
        print("Feature importance:", payload["feature_importance"])

    print("\nPer-crop F1 (holdout):")
    for label in labels:
        print(f"  {label:<15} precision {report[label]['precision']:.2f} recall {report[label]['recall']:.2f} f1 {report[label]['f1-score']:.2f}")


def pickle_dump(model, handle):
    import pickle

    pickle.dump(model, handle)


if __name__ == "__main__":
    main()
