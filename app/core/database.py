"""Database engine, session factory, and the base class for ORM models."""

from collections.abc import Iterator

# SQLAlchemy maps Python objects to database rows and generates the SQL.
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# The engine owns a pool of open connections. Opening a TCP connection and
# authenticating costs milliseconds, so connections are reused rather than
# recreated per request.
#
# pool_pre_ping sends a cheap "SELECT 1" before handing a connection out. A
# connection that died while idle (database restarted, network dropped) is then
# replaced instead of failing a real request with a confusing error.
engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=settings.db_echo,
)

# A Session is one conversation with the database: it tracks the objects it has
# loaded, holds a transaction, and writes changes out. One session per request.
#
# expire_on_commit=False keeps attribute values loaded after a commit. With the
# default (True), reading ticket.id after committing would trigger another
# SELECT, which is a wasted round trip for objects we are about to return.
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for ORM models. It collects their table definitions."""


def get_session() -> Iterator[Session]:
    """Provide a session for one request and always close it.

    A dependency written with yield runs the code before yield on the way in and
    the code after it on the way out, including when the endpoint raises. Closing
    returns the connection to the pool - forgetting to would exhaust it.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()