from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import kagglehub

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    confusion_matrix,
    classification_report,
)

DATASET_SLUG = "datasetengineer/crop-health-and-environmental-stress-dataset"

FEATURES = [
    "NDVI",
    "SAVI",
    "Temperature",
    "Humidity",
    "Rainfall",
    "Wind_Speed",
    "Soil_Moisture",
    "Soil_pH",
]

TARGET = "Crop_Health_Label"

TEST_SIZE = 0.2
RANDOM_STATE = 42


def load_dataset():
    print(f"Downloading/locating dataset '{DATASET_SLUG}'...")

    dataset_path = Path(kagglehub.dataset_download(DATASET_SLUG))

    csv_files = sorted(dataset_path.glob("*.csv"))

    if not csv_files:
        sys.exit(f"No CSV file found under {dataset_path}")

    csv_path = max(csv_files, key=lambda p: p.stat().st_size)

    print(f"Loading: {csv_path.name}")

    return pd.read_csv(csv_path)


def inspect_dataset(df):
    print("\n=== DATASET INSPECTION ===")
    print(f"Rows: {len(df):,}")
    print(f"Columns: {len(df.columns)}")

    required = FEATURES + [TARGET]

    missing = [col for col in required if col not in df.columns]

    if missing:
        sys.exit(f"Missing columns: {missing}")

    print("\nTarget distribution:")
    print(df[TARGET].value_counts())

    print("\nMissing values:")
    print(df[required].isna().sum())

    print("\nFeature correlations with target:")

    print(
        df[required]
        .corr(numeric_only=True)[TARGET]
        .drop(TARGET)
        .sort_values()
    )


def prepare_data(df):
    data = df[FEATURES + [TARGET]].copy()

    for feature in FEATURES:
        data[feature] = pd.to_numeric(
            data[feature],
            errors="coerce"
        )

    data[TARGET] = pd.to_numeric(
        data[TARGET],
        errors="coerce"
    )

    data = data.dropna(subset=[TARGET])

    data[TARGET] = data[TARGET].astype(int)

    X = data[FEATURES]
    y = data[TARGET]

    return X, y


def train_model(X, y):
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=y
    )

    print("\n=== TRAINING ===")
    print(f"Train rows: {len(X_train):,}")
    print(f"Test rows: {len(X_test):,}")
    print(f"Features: {len(FEATURES)}")

    model = HistGradientBoostingClassifier(
        max_iter=300,
        max_depth=6,
        learning_rate=0.06,
        random_state=RANDOM_STATE
    )

    model.fit(X_train, y_train)

    predictions = model.predict(X_test)
    probabilities = model.predict_proba(X_test)[:, 1]

    return model, y_test, predictions, probabilities


def evaluate(y_test, predictions, probabilities):
    accuracy = accuracy_score(y_test, predictions)
    precision = precision_score(y_test, predictions, zero_division=0)
    recall = recall_score(y_test, predictions, zero_division=0)
    f1 = f1_score(y_test, predictions, zero_division=0)
    auc = roc_auc_score(y_test, probabilities)

    cm = confusion_matrix(y_test, predictions)

    print("\n=== RESULTS ===")

    print(f"Accuracy : {accuracy:.4f}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall   : {recall:.4f}")
    print(f"F1 Score : {f1:.4f}")
    print(f"ROC-AUC  : {auc:.4f}")

    print("\n=== CONFUSION MATRIX ===")
    print(cm)

    print("\n=== CLASSIFICATION REPORT ===")
    print(
        classification_report(
            y_test,
            predictions,
            target_names=["Unhealthy", "Healthy"],
            zero_division=0
        )
    )

    print("\n=== SAMPLE PREDICTIONS ===")

    for actual, predicted, probability in zip(
        y_test.iloc[:10],
        predictions[:10],
        probabilities[:10]
    ):
        print(
            f"Actual: {actual} | "
            f"Predicted: {predicted} | "
            f"Healthy probability: {probability:.3f}"
        )


def main():
    df = load_dataset()

    inspect_dataset(df)

    X, y = prepare_data(df)

    model, y_test, predictions, probabilities = train_model(X, y)

    evaluate(
        y_test,
        predictions,
        probabilities
    )


if __name__ == "__main__":
    main()