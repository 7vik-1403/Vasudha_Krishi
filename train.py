"""Retrain and compare crop recommendation models.

Usage:
    python train.py               # evaluate all candidates, save the best model
    python train.py --model rf    # force a specific candidate (gnb|rf|svc|knn|logreg)
"""

import argparse
import json
import os
import warnings
from datetime import datetime

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
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
    return frame


def evaluate(frame, only=None):
    x = frame[FEATURE_KEYS]
    y = frame[TARGET]

    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=0.2, random_state=SEED, stratify=y
    )

    results = []
    for key, (label, model) in CANDIDATES.items():
        if only and key != only:
            continue
        scores = cross_val_score(model, x_train, y_train, cv=5, scoring="accuracy", n_jobs=1)
        model.fit(x_train, y_train)
        predictions = model.predict(x_test)
        entry = {
            "id": key,
            "label": label,
            "cv_accuracy_mean": round(float(scores.mean()), 4),
            "cv_accuracy_std": round(float(scores.std()), 4),
            "holdout_accuracy": round(float(accuracy_score(y_test, predictions)), 4),
            "holdout_macro_f1": round(float(f1_score(y_test, predictions, average="macro")), 4),
        }
        results.append(entry)
        print(
            f"{label:<36} CV {entry['cv_accuracy_mean']:.4f} +/- {entry['cv_accuracy_std']:.4f} "
            f"| holdout acc {entry['holdout_accuracy']:.4f} | macro-F1 {entry['holdout_macro_f1']:.4f}"
        )

    results.sort(key=lambda item: (item["holdout_macro_f1"], item["holdout_accuracy"]), reverse=True)
    return results, (x_train, x_test, y_train, y_test)


def main():
    parser = argparse.ArgumentParser(description="Train the crop recommendation model")
    parser.add_argument("--model", choices=sorted(CANDIDATES), default=None, help="force one candidate")
    args = parser.parse_args()

    warnings.filterwarnings("ignore")
    frame = load_data()
    print(f"Dataset: {len(frame)} rows, {frame[TARGET].nunique()} crops, features {FEATURE_KEYS}")

    results, (x_train, x_test, y_train, y_test) = evaluate(frame, only=args.model)
    best = results[0]
    print(f"\nBest model: {best['label']} (holdout accuracy {best['holdout_accuracy']:.4f})")

    model = CANDIDATES[best["id"]][1]
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    report = classification_report(y_test, predictions, output_dict=True, zero_division=0)
    labels = sorted(frame[TARGET].unique())
    matrix = confusion_matrix(y_test, predictions, labels=labels)

    with open(MODEL_PATH, "wb") as fh:
        pickle_dump(model, fh)
    print(f"Saved model -> {MODEL_PATH}")

    feature_importance = None
    estimator = model.named_steps.get("rf") if hasattr(model, "named_steps") else model
    if hasattr(estimator, "feature_importances_"):
        feature_importance = {
            key: round(float(value), 4) for key, value in zip(FEATURE_KEYS, estimator.feature_importances_)
        }

    payload = {
        "trained_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "sklearn_version": __import__("sklearn").__version__,
        "dataset_rows": int(len(frame)),
        "classes": labels,
        "features": FEATURE_KEYS,
        "candidates": results,
        "best": best,
        "holdout_accuracy": best["holdout_accuracy"],
        "holdout_macro_f1": best["holdout_macro_f1"],
        "classification_report": report,
        "confusion_matrix": matrix.tolist(),
        "confusion_matrix_labels": labels,
        "feature_importance": feature_importance,
        "caveat": (
            "The public Crop Recommendation dataset has narrow, well-separated per-crop feature ranges, "
            "so random-split scores are optimistically high compared to real fields. "
            "Treat them as a relative ranking of models, not a field accuracy claim."
        ),
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print(f"Saved training report -> {REPORT_PATH}")

    if feature_importance:
        print("Feature importance:", feature_importance)

    print("\nPer-crop F1 (holdout):")
    for label in labels:
        print(f"  {label:<15} precision {report[label]['precision']:.2f} recall {report[label]['recall']:.2f} f1 {report[label]['f1-score']:.2f}")


def pickle_dump(model, handle):
    import pickle

    pickle.dump(model, handle)


if __name__ == "__main__":
    main()
