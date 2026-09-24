"""Storage for tickets, backed by PostgreSQL."""

from contextlib import contextmanager
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.tickets.models import Ticket


class StorageError(RuntimeError):
    """Storage is unavailable or a query failed.

    The service and router must not depend on SQLAlchemy's exception types, or
    swapping the database would change code in every layer. Database errors are
    translated here into one error that the upper layers own.
    """


class TicketRepository(Protocol):
    """What the service needs from storage.

    A Protocol describes a shape rather than a base class: any object with these
    methods satisfies it, without inheriting anything. The service depends on
    this, not on PostgreSQL.
    """

    def add(self, ticket: Ticket) -> Ticket: ...

    def get(self, ticket_id: int) -> Ticket | None: ...

    def list(
        self,
        label: str | None = None,
        needs_review: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Ticket]: ...

    def count(
        self, label: str | None = None, needs_review: bool | None = None
    ) -> int: ...


@contextmanager
def _translated_errors(session: Session):
    """Turn any SQLAlchemy failure into StorageError, leaving no open transaction.

    A failed statement leaves the session in a broken state until it is rolled
    back; the next query on it would fail for the wrong reason and hide the real
    cause.
    """
    try:
        yield
    except SQLAlchemyError as error:
        session.rollback()
        raise StorageError(str(error)) from error


class PostgresTicketRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, ticket: Ticket) -> Ticket:
        with _translated_errors(self._session):
            self._session.add(ticket)
            # commit writes the transaction durably and makes the row visible to
            # every other connection and process. The id comes from a database
            # sequence, so two processes inserting at the same moment cannot
            # receive the same id - the failure demonstrated in v1.
            self._session.commit()
        return ticket

    def get(self, ticket_id: int) -> Ticket | None:
        with _translated_errors(self._session):
            # session.get() looks up by primary key and returns None if absent.
            return self._session.get(Ticket, ticket_id)

    def _filtered(self, statement, label: str | None, needs_review: bool | None):
        """Apply the same WHERE conditions to any statement.

        list() and count() must filter identically, or the reported total would
        describe a different set of rows than the page returned. Sharing one
        function is what guarantees that, instead of two copies staying in step
        by luck.
        """
        if label is not None:
            statement = statement.where(Ticket.label == label)
        if needs_review is not None:
            statement = statement.where(Ticket.needs_review == needs_review)
        return statement

    def list(
        self,
        label: str | None = None,
        needs_review: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Ticket]:
        with _translated_errors(self._session):
            # select() builds a SQL query. Filters become WHERE conditions, so
            # PostgreSQL does the filtering instead of Python loading every row.
            statement = self._filtered(select(Ticket), label, needs_review)

            # ORDER BY must come before LIMIT, and must be on something stable.
            # Without a deterministic order, "the first 50 rows" is whatever the
            # database happens to return, and two pages could overlap or skip.
            # id is immutable and unique, which makes it a safe sort key.
            statement = statement.order_by(Ticket.id).limit(limit).offset(offset)

            # scalars() returns the Ticket objects rather than one-column rows.
            return list(self._session.scalars(statement))

    def count(
        self, label: str | None = None, needs_review: bool | None = None
    ) -> int:
        with _translated_errors(self._session):
            # COUNT(*) is computed by the database; no rows are transferred.
            # It still reads every matching row internally, so it gets slower as
            # the table grows - a cost worth measuring rather than assuming.
            statement = self._filtered(select(func.count()).select_from(Ticket), label, needs_review)
            return self._session.scalar(statement) or 0