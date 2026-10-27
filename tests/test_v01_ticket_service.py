"""Unit tests for ticket business logic - no HTTP, no real model, no server."""

import pytest

from app.classification.classifier import Prediction
from app.tickets.service import TicketService
from tests.fakes import InMemoryTicketRepository

# Every service call now names the organisation it acts for. These tests do not
# go through HTTP, so there is no token to read it from - the id is supplied
# directly, which is exactly how a background worker would call the service.
ORG = 1
OTHER_ORG = 2


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

    ticket = service.submit(ORG, "I was charged twice")

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

    assert service.submit(ORG, "some text").needs_review is expected_review


def test_submit_passes_the_text_to_the_classifier():
    classifier = FakeClassifier()
    service = TicketService(
        repository=InMemoryTicketRepository(),
        classifier=classifier,
        low_confidence_threshold=0.55,
    )

    service.submit(ORG, "The app crashes on upload")

    assert classifier.calls == ["The app crashes on upload"]


def test_get_returns_none_for_unknown_id():
    # The service does not raise and does not know about HTTP status codes;
    # turning None into a 404 is the router's job.
    assert build_service().get(ORG, 999) is None


def test_list_filters_are_combined():
    repository = InMemoryTicketRepository()
    high = TicketService(repository, FakeClassifier("billing", 0.9), 0.55)
    low = TicketService(repository, FakeClassifier("billing", 0.2), 0.55)
    other = TicketService(repository, FakeClassifier("technical_issue", 0.9), 0.55)

    high.submit(ORG, "confident billing")
    low.submit(ORG, "unsure billing")
    other.submit(ORG, "confident technical")

    assert len(high.list(ORG).items) == 3
    assert len(high.list(ORG, label="billing").items) == 2
    assert len(high.list(ORG, needs_review=True).items) == 1
    assert len(high.list(ORG, label="billing", needs_review=True).items) == 1


def test_classifier_failure_propagates():
    class BrokenClassifier:
        def predict(self, text):
            raise RuntimeError("model exploded")

    service = TicketService(InMemoryTicketRepository(), BrokenClassifier(), 0.55)

    # The service does not swallow the error - the router decides it is a 500.
    with pytest.raises(RuntimeError, match="model exploded"):
        service.submit(ORG, "anything")

# --- Pagination, without HTTP -----------------------------------------------


def build_paged_service(
    default_page_size: int = 50, max_page_size: int = 200
) -> TicketService:
    return TicketService(
        repository=InMemoryTicketRepository(),
        classifier=FakeClassifier(),
        low_confidence_threshold=0.55,
        default_page_size=default_page_size,
        max_page_size=max_page_size,
    )


def test_list_uses_the_default_page_size_when_none_is_given():
    service = build_paged_service(default_page_size=2)
    for i in range(5):
        service.submit(ORG, f"ticket {i}")

    page = service.list(ORG)

    assert page.limit == 2
    assert len(page.items) == 2
    assert page.total == 5
    assert page.offset == 0


# The service must defend the ceiling itself, because a worker or a CLI can
# call it with no HTTP validation in front of it.
@pytest.mark.parametrize(
    "requested_limit, expected_limit",
    [(1, 1), (5, 5), (10, 10), (11, 10), (10_000, 10), (0, 1), (-5, 1)],
)
def test_service_clamps_the_limit(requested_limit, expected_limit):
    service = build_paged_service(max_page_size=10)

    assert service.list(ORG, limit=requested_limit).limit == expected_limit


def test_service_clamps_a_negative_offset():
    service = build_paged_service()

    assert service.list(ORG, offset=-10).offset == 0


def test_total_is_independent_of_the_page():
    service = build_paged_service()
    for i in range(6):
        service.submit(ORG, f"ticket {i}")

    page = service.list(ORG, limit=2, offset=4)

    assert len(page.items) == 2
    assert page.total == 6


def test_one_organisation_cannot_see_another_organisations_tickets():
    """The whole reason organization_id threads through every method.

    Proved at the service layer as well as through HTTP, because this is where
    the filter is actually passed on. An HTTP-only test would still pass if the
    service ignored its argument and the router happened to be right.
    """
    repository = InMemoryTicketRepository()
    service = TicketService(repository, FakeClassifier(), 0.55)

    mine = service.submit(ORG, "my ticket")
    service.submit(OTHER_ORG, "their ticket")

    assert service.list(ORG).total == 1
    assert service.list(OTHER_ORG).total == 1

    # Reading another organisation's ticket by id returns None, not the ticket.
    # The router turns that into 404 rather than 403: confirming the row exists
    # would itself leak information across the boundary.
    assert service.get(OTHER_ORG, mine.id) is None
    assert service.get(ORG, mine.id) is not None
