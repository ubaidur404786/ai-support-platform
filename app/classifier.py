"""Loads the trained ticket classifier and runs predictions."""

import logging
from dataclasses import dataclass
from pathlib import Path

import joblib

logger = logging.getLogger(__name__)


# A dataclass is a small class that only holds data; Python writes __init__ for us.
@dataclass(frozen=True)
class Prediction:
    label: str
    confidence: float
    model_version: str


class TicketClassifier:
    """Thin wrapper around the scikit-learn pipeline saved by ml/train.py.

    The API talks only to this class. If the model changes later (different
    algorithm, or a remote model service), the API code stays the same.
    """

    def __init__(self, pipeline, model_version: str, labels: list[str]) -> None:
        self._pipeline = pipeline
        self.model_version = model_version
        self.labels = labels

    @classmethod
    def load(cls, path: str | Path) -> "TicketClassifier":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"Model file not found: {path}. Run 'python ml/train.py'first."
            )
        bundle = joblib.load(path)
        logger.info("Loaded classifier %s from %s",bundle["model_version"], path)
        return cls(bundle["pipeline"], bundle["model_version"],bundle["labels"])

    def predict(self, text: str) -> Prediction:
        # predict_proba gives one probability per class. The highest oneis the
        # answer, and its value is what we report as "confidence".
        probabilities = self._pipeline.predict_proba([text])[0]
        best_index = int(probabilities.argmax())
        return Prediction(
            label=str(self._pipeline.classes_[best_index]),
            confidence=float(probabilities[best_index]),
            model_version=self.model_version,
        )