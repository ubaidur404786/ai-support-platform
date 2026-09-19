"""Train and evaluate the v0 ticket classifier.

Run from the repository root:
    python ml/train.py

Produces:
    models/ticket_classifier.joblib  - the model the API will load
    models/metrics.json              - evaluation results for documentation
"""
import sklearn
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

# joblib saves Python objects (here: a trained model) to disk and loads them back.
import joblib

# scikit-learn provides the classical ML building blocks used below.
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

# Resolve paths from this file's location so the script works from any directory.
ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "ml" / "data" / "tickets.csv"
MODEL_DIR = ROOT / "models"
MODEL_PATH = MODEL_DIR / "ticket_classifier.joblib"
METRICS_PATH = MODEL_DIR / "metrics.json"

# A version string travels with the model so the API can report which model answered.
MODEL_VERSION = "v0.1.0"

# A fixed seed makes the train/test split and training reproducible.
RANDOM_SEED = 42
TEST_FRACTION = 0.25


def load_dataset(path: Path) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    labels: list[str] = []
    with path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            texts.append(row["text"])
            labels.append(row["label"])
    return texts, labels


def build_pipeline() -> Pipeline:
    # A Pipeline chains preprocessing and the model into one object.
    # This matters for serving: the API calls one .predict() and the exact same
    # text preprocessing used in training is applied automatically.
    return Pipeline(
        [
            
            # TF-IDF converts text into numeric features. ngram_range=(1, 3) means
            # single words, word pairs and word triples are all used as features.
            ("tfidf", TfidfVectorizer(ngram_range=(1, 3), lowercase=True)),
            # Logistic Regression is a small, fast linear classifier that also
            # gives a probability for each class, which we report as "confidence".
            ("classifier", LogisticRegression(C=10.0, max_iter=1000, random_state=RANDOM_SEED)),
        ]
    )


def main() -> None:
    texts, labels = load_dataset(DATA_PATH)
    print(f"Loaded {len(texts)} examples with {len(set(labels))} labels: {sorted(set(labels))}")

    # Hold out part of the data that the model never sees during training.
    # Evaluating on unseen data is the only honest way to measure a model.
    # stratify=labels keeps the same label proportions in both splits.
    x_train, x_test, y_train, y_test = train_test_split(
        texts, labels, test_size=TEST_FRACTION, random_state=RANDOM_SEED, stratify=labels
    )
    print(f"Training on {len(x_train)} examples, evaluating on {len(x_test)} examples")

    pipeline = build_pipeline()
    pipeline.fit(x_train, y_train)

    predictions = pipeline.predict(x_test)
    accuracy = accuracy_score(y_test, predictions)
    # "macro" F1 averages F1 over classes equally, so a rare class counts as much as a common one.
    macro_f1 = f1_score(y_test, predictions, average="macro")

    print("\nEvaluation on held-out test set:")
    print(f"  accuracy : {accuracy:.3f}")
    print(f"  macro F1 : {macro_f1:.3f}\n")
    print(classification_report(y_test, predictions, zero_division=0))

    MODEL_DIR.mkdir(exist_ok=True)

    # Bundle the model with its metadata so the serving code never guesses.
    bundle = {
        "pipeline": pipeline,
        "model_version": MODEL_VERSION,
        "labels": sorted(set(labels)),
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    joblib.dump(bundle, MODEL_PATH)

    metrics = {
        "model_version": MODEL_VERSION,
        "sklearn_version": sklearn.__version__,
        "n_train": len(x_train),
        "n_test": len(x_test),
        "accuracy": round(accuracy, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": classification_report(y_test, predictions, output_dict=True, zero_division=0),
    }
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(f"Saved model   -> {MODEL_PATH}")
    print(f"Saved metrics -> {METRICS_PATH}")


if __name__ == "__main__":
    main()