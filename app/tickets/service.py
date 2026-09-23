"""Business logic for tickets: classify on submission and flag uncertain results."""

import logging

from app.classification.classifier import TicketClassifier
from app.tickets.models import Ticket
from app.tickets.repository import TicketRepository

logger = logging.getLogger(__name__)


class TicketService:
    def __init__(
        self,
        repository: TicketRepository,
        classifier: TicketClassifier,
        low_confidence_threshold: float,
    ) -> None:
        # The service is given what it needs instead of creating it. That is what
        # makes it testable without HTTP, without a real model, and without storage.
        self._repository = repository
        self._classifier = classifier
        self._low_confidence_threshold = low_confidence_threshold

    def submit(self, text: str) -> Ticket:
        prediction = self._classifier.predict(text)

        # A low-confidence prediction is not an error: we still store the ticket,
        # but mark it so a human can check the routing before it is acted on.
        needs_review = prediction.confidence < self._low_confidence_threshold
        if needs_review:
            logger.info(
                "Ticket flagged for review: label=%s confidence=%.3f threshold=%.2f",
                prediction.label,
                prediction.confidence,
                self._low_confidence_threshold,
            )

        ticket = Ticket(
            
            text=text,
            label=prediction.label,
            confidence=prediction.confidence,
            model_version=prediction.model_version,
            needs_review=needs_review,
        )
        return self._repository.add(ticket)

    def get(self, ticket_id: int) -> Ticket | None:
        return self._repository.get(ticket_id)

    def list(self, label: str | None = None, needs_review: bool | None = None):
        return self._repository.list(label=label, needs_review=needs_review)