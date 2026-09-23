"""Wiring for the tickets module."""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.classification.classifier import TicketClassifier
from app.classification.dependencies import get_classifier
from app.core.config import settings
from app.core.database import get_session
from app.tickets.repository import PostgresTicketRepository, TicketRepository
from app.tickets.service import TicketService


def get_repository(session: Session = Depends(get_session)) -> TicketRepository:
    # A repository per request, bound to that request's session. It is no longer
    # shared application state: the shared thing is now the database itself.
    return PostgresTicketRepository(session)


def get_ticket_service(
    repository: TicketRepository = Depends(get_repository),
    classifier: TicketClassifier = Depends(get_classifier),
) -> TicketService:
    return TicketService(
        repository=repository,
        classifier=classifier,
        low_confidence_threshold=settings.low_confidence_threshold,
    )