"""Identity: who is making a request, and which organisation they belong to.

These are the platform's own objects, mapped to database tables - the same
separation the tickets module uses. API schemas live elsewhere, because what we
store and what we send over HTTP are not the same thing. A password hash is
stored and must never be sent.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Organization(Base):
    """A tenant: the unit that owns data.

    Everything in this system belongs to exactly one organisation. This is the
    boundary that authorization is built on - a query that does not filter by
    organisation is a query that can leak one customer's data to another.
    """

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)

    # unique=True creates a unique index. The database, not the application,
    # refuses a duplicate. Checking "does this name exist?" in Python first is
    # not enough: two requests can both check, both find nothing, and both
    # insert. Only the database sees both at once.
    name: Mapped[str] = mapped_column(String(200), unique=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    """A person who can authenticate, belonging to exactly one organisation."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)

    # A ForeignKey tells PostgreSQL this value must match a real organizations.id.
    # The database then refuses to create a user in an organisation that does not
    # exist, and refuses to delete an organisation that still has users. That
    # refusal is the point: an application bug cannot produce an orphaned row.
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )

    # 320 characters is the longest address the email standard allows.
    #
    # Addresses are stored lowercase and normalised on the way in. Without that,
    # "Maya@example.com" and "maya@example.com" are two different rows, and a
    # user who capitalises their address at signup cannot log in later.
    email: Mapped[str] = mapped_column(String(320), unique=True)

    # Named password_hash, not password, so there is nowhere to put a plaintext
    # password even by mistake. 255 characters fits bcrypt (60) and argon2 (~100)
    # with room for a future change of algorithm.
    password_hash: Mapped[str] = mapped_column(String(255))

    # Disabling beats deleting. A deleted user breaks every row that referenced
    # them - including, later, the record of who reviewed which ticket. History
    # must stay readable after someone leaves.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
