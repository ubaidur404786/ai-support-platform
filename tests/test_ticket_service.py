"""Unit tests for ticket business logic - no HTTP, no real model, no server."""

import pytest

from app.classification.classifier import Prediction
# from app.tickets.repository import InMemoryTicketRepository
from app.tickets.service import TicketService
from tests.fakes import InMemoryTicketRepository

class FakeClassifier:
    """Returns a fixed prediction, so tests control confidence exactly.

    Using a fake instead of the real model makes these tests fast and
    deterministic: we are testing our logic, not the model's accuracy.
    """

    def __init__(self, label: str = "billing", confidence: float = 0.9) -> None:
        self._prediction = Prediction(
            label=label, confidence=confidence, model_version="test-1.0"
        )
        self.calls: list[str] = []

    def predict(self, text: str) -> Prediction:
        self.calls.append(text)
        return self._prediction


def build_service(confidence: float = 0.9, threshold: float = 0.55) -> TicketService:
    return TicketService(
        repository=InMemoryTicketRepository(),
        classifier=FakeClassifier(confidence=confidence),
        low_confidence_threshold=threshold,
    )


def test_submit_stores_the_classification_result():
    service = build_service(confidence=0.9)

    ticket = service.submit("I was charged twice")

    assert ticket.id == 1
    assert ticket.text == "I was charged twice"
    assert ticket.label == "billing"
    assert ticket.confidence == 0.9
    assert ticket.model_version == "test-1.0"


# The threshold is a boundary: test just below, exactly on, and above it.
@pytest.mark.parametrize(
    "confidence, expected_review",
    [(0.54, True), (0.55, False), (0.56, False), (0.0, True), (1.0, False)],
)
def test_review_flag_follows_the_threshold(confidence, expected_review):
    service = build_service(confidence=confidence, threshold=0.55)

    assert service.submit("some text").needs_review is expected_review


def test_submit_passes_the_text_to_the_classifier():
    classifier = FakeClassifier()
    service = TicketService(
        repository=InMemoryTicketRepository(),
        classifier=classifier,
        low_confidence_threshold=0.55,
    )

    service.submit("The app crashes on upload")

    assert classifier.calls == ["The app crashes on upload"]


def test_get_returns_none_for_unknown_id():
    # The service does not raise and does not know about HTTP status codes;
    # turning None into a 404 is the router's job.
    assert build_service().get(999) is None


def test_list_filters_are_combined():
    repository = InMemoryTicketRepository()
    high = TicketService(repository, FakeClassifier("billing", 0.9), 0.55)
    low = TicketService(repository, FakeClassifier("billing", 0.2), 0.55)
    other = TicketService(repository, FakeClassifier("technical_issue", 0.9), 0.55)

    high.submit("confident billing")
    low.submit("unsure billing")
    other.submit("confident technical")

    assert len(high.list()) == 3
    assert len(high.list(label="billing")) == 2
    assert len(high.list(needs_review=True)) == 1
    assert len(high.list(label="billing", needs_review=True)) == 1


def test_classifier_failure_propagates():
    class BrokenClassifier:
        def predict(self, text):
            raise RuntimeError("model exploded")

    service = TicketService(InMemoryTicketRepository(), BrokenClassifier(), 0.55)

    # The service does not swallow the error - the router decides it is a 500.
    with pytest.raises(RuntimeError, match="model exploded"):
        service.submit("anything")