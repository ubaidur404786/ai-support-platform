"""Business logic for tickets: classify on submission and flag uncertain results."""

import logging
from dataclasses import dataclass

from app.classification.classifier import TicketClassifier
from app.tickets.models import Ticket
from app.tickets.repository import TicketRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TicketPage:
    """One window onto the ticket list, plus how big the whole list is.

    Returning a small object instead of a bare list keeps the two numbers that
    belong together - what you got, and what exists - from drifting apart as
    they are passed between layers.
    """

    items: list[Ticket]
    total: int
    limit: int
    offset: int


class TicketService:
    def __init__(
        self,
        repository: TicketRepository,
        classifier: TicketClassifier,
        low_confidence_threshold: float,
        default_page_size: int = 50,
        max_page_size: int = 200,
    ) -> None:
        # The service is given what it needs instead of creating it. That is what
        # makes it testable without HTTP, without a real model, and without storage.
        self._repository = repository
        self._classifier = classifier
        self._low_confidence_threshold = low_confidence_threshold
        self._default_page_size = default_page_size
        self._max_page_size = max_page_size

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
            # No id here on purpose: PostgreSQL assigns it from a sequence.
            # Setting it explicitly would bypass the sequence and collide.
            text=text,
            label=prediction.label,
            confidence=prediction.confidence,
            model_version=prediction.model_version,
            needs_review=needs_review,
        )
        return self._repository.add(ticket)

    def get(self, ticket_id: int) -> Ticket | None:
        return self._repository.get(ticket_id)

    def list(
        self,
        label: str | None = None,
        needs_review: bool | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> TicketPage:
        # The router already rejects an oversized limit, but the service must not
        # depend on that. A background worker or a CLI calls this directly, with
        # no HTTP validation in front of it, so the ceiling is enforced here too.
        # Defending a boundary at both sides of it is called defence in depth.
        if limit is None:
            limit = self._default_page_size
        limit = max(1, min(limit, self._max_page_size))
        offset = max(0, offset)

        items = self._repository.list(
            label=label, needs_review=needs_review, limit=limit, offset=offset
        )
        # A second query, because "how many are there" cannot be answered by a
        # page: len(items) is at most `limit`. The filters must match exactly.
        total = self._repository.count(label=label, needs_review=needs_review)

        return TicketPage(items=items, total=total, limit=limit, offset=offset)