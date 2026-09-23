"""The Ticket domain object.

This is the platform's own representation of a ticket, separate from the API
schemas. The API can change its JSON shape, and storage can change from a dict
to a database, without either one dictating what a ticket *is*.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Ticket:
    id: int
    text: str
    label: str
    confidence: float
    model_version: str
    needs_review: bool
    created_at: datetime = field(default_factory=_now)