"""Storage for tickets.

Everything that touches storage goes through this class. Today it is a
dictionary in memory; in a later version the same methods will run SQL against
PostgreSQL, and the service and router will not change.
"""

import threading

from app.tickets.models import Ticket


class InMemoryTicketRepository:
    def __init__(self) -> None:
        self._tickets: dict[int, Ticket] = {}
        self._next_id = 1
        # FastAPI runs synchronous endpoints in a thread pool, so two requests
        # can reach this object at the same time. Without a lock, both could
        # read the same _next_id and one ticket would overwrite the other.
        self._lock = threading.Lock()

    def add(self, ticket: Ticket) -> Ticket:
        with self._lock:
            ticket.id = self._next_id
            self._next_id += 1
            self._tickets[ticket.id] = ticket
        return ticket

    def get(self, ticket_id: int) -> Ticket | None:
        return self._tickets.get(ticket_id)

    def list(self, label: str | None = None, needs_review: bool | None = None) -> list[Ticket]:
        tickets = list(self._tickets.values())
        if label is not None:
            tickets = [t for t in tickets if t.label == label]
        if needs_review is not None:
            tickets = [t for t in tickets if t.needs_review is needs_review]
        return sorted(tickets, key=lambda t: t.id)

    def count(self) -> int:
        return len(self._tickets)