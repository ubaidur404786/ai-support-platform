"""Test doubles used by tests that do not need a real database."""

import threading
from datetime import datetime, timezone

from app.tickets.models import Ticket


class InMemoryTicketRepository:
    """Stands in for PostgreSQL in tests that only exercise business logic.

    It satisfies the same TicketRepository protocol, so TicketService cannot tell
    the difference. Tests using it need no database and no network.

    Note the risk this carries: a fake can behave differently from the real
    thing, and a test can pass here while failing against PostgreSQL. It is used
    only for logic that does not depend on storage semantics.
    """

    def __init__(self) -> None:
        self._tickets: dict[int, Ticket] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def add(self, ticket: Ticket) -> Ticket:
        with self._lock:
            ticket.id = self._next_id
            self._next_id += 1
            # SQLAlchemy applies the created_at default when writing to the
            # database. Nothing writes here, so the fake fills it in.
            if ticket.created_at is None:
                ticket.created_at = datetime.now(timezone.utc)
            self._tickets[ticket.id] = ticket
        return ticket

    def get(self, ticket_id: int) -> Ticket | None:
        return self._tickets.get(ticket_id)

    def _filtered(
        self, label: str | None, needs_review: bool | None
    ) -> list[Ticket]:
        tickets = list(self._tickets.values())
        if label is not None:
            tickets = [t for t in tickets if t.label == label]
        if needs_review is not None:
            tickets = [t for t in tickets if t.needs_review is needs_review]
        return sorted(tickets, key=lambda t: t.id)

    def list(
        self,
        label: str | None = None,
        needs_review: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Ticket]:
        # A Python slice stands in for SQL's LIMIT/OFFSET. Note what this fake
        # does NOT reproduce: the real database discards `offset` rows before
        # returning any, and that work grows with depth. A fake can hide a cost.
        return self._filtered(label, needs_review)[offset : offset + limit]

    def count(
        self, label: str | None = None, needs_review: bool | None = None
    ) -> int:
        return len(self._filtered(label, needs_review))