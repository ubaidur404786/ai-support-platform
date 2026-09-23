"""The Ticket domain object, mapped to the tickets table.

This is still the platform's own representation of a ticket, separate from the
API schemas in schemas.py. It now also describes how a ticket is stored.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Ticket(Base):
    __tablename__ = "tickets"

    # The database generates ids from a sequence. Two processes inserting at the
    # same moment can no longer receive the same id - exactly the problem the
    # two-server test exposed in v1.
    id: Mapped[int] = mapped_column(primary_key=True)

    # Text has no length limit; String(n) does. Ticket bodies vary, labels do not.
    text: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(50))
    confidence: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(50))
    needs_review: Mapped[bool] = mapped_column(Boolean)

    # timezone=True stores the UTC offset. Without it PostgreSQL discards the
    # offset and a stored "10:00" becomes ambiguous.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # An index lets PostgreSQL jump to matching rows instead of reading the whole
    # table. We index label because it has many distinct values, which makes a
    # lookup selective.
    #
    # We deliberately do NOT index needs_review: it is a boolean, and in our data
    # roughly half the rows could match, so scanning is often cheaper than using
    # an index. If the flagged fraction turns out to be small in practice, the
    # right tool is a partial index (WHERE needs_review), added after measuring.
    __table_args__ = (Index("ix_tickets_label", "label"),)