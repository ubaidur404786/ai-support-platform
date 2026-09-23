"""Wiring for the tickets module."""

from fastapi import Depends, Request

from app.classification.classifier import TicketClassifier
from app.classification.dependencies import get_classifier
from app.core.config import settings
from app.tickets.repository import InMemoryTicketRepository
from app.tickets.service import TicketService


def get_repository(request: Request) -> InMemoryTicketRepository:
    # One repository for the whole process, created at startup, so tickets
    # submitted by one request are visible to the next one.
    return request.app.state.ticket_repository


def get_ticket_service(
    repository: InMemoryTicketRepository = Depends(get_repository),
    classifier: TicketClassifier = Depends(get_classifier),
) -> TicketService:
    return TicketService(
        repository=repository,
        classifier=classifier,
        low_confidence_threshold=settings.low_confidence_threshold,
    )