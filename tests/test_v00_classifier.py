
"""Tests for the model wrapper, independent of HTTP."""

import pytest

from app.classification.classifier import TicketClassifier
from app.core.config import settings


@pytest.fixture(scope="module")
def classifier():
    return TicketClassifier.load(settings.classifier_path)


def test_load_missing_file_raises_clear_error():
    with pytest.raises(FileNotFoundError, match="ml/train.py"):
        TicketClassifier.load("models/does_not_exist.joblib")


def test_prediction_has_valid_shape(classifier):
    prediction = classifier.predict("The app crashes every time I open the reports page")

    assert prediction.label in classifier.labels
    assert 0.0 <= prediction.confidence <= 1.0
    assert prediction.model_version == "v0.1.0"


# These are behaviour tests on the model itself. They are deliberately "obvious"
# tickets; if one fails after retraining, the new model got worse on a basic case.
@pytest.mark.parametrize(
    "text, expected_label",
    [
        ("I need a refund for the payment I made yesterday", "billing"),
        ("I forgot my password and cannot log in", "account_access"),
        ("Please add a dark mode option to the app", "feature_request"),
        ("The page shows a 500 error when I upload a file", "technical_issue"),
    ],
)
def test_obvious_examples(classifier, text, expected_label):
    assert classifier.predict(text).label == expected_label