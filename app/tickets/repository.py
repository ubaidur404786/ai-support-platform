"""Storage for tickets, backed by PostgreSQL."""

from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import translated_errors
from app.tickets.models import Ticket


class TicketRepository(Protocol):
    """What the service needs from storage.

    A Protocol describes a shape rather than a base class: any object with these
    methods satisfies it, without inheriting anything. The service depends on
    this, not on PostgreSQL.
    """

    # organization_id is required and comes first on every read. It is not an
    # optional filter with a "no filter" default: forgetting a required argument
    # is a TypeError at the call site, while forgetting an optional one would
    # silently return every organisation's rows and pass every test.
    #
    # add() needs no extra parameter - the Ticket object already carries its
    # organization_id, set by the service from the authenticated caller.
    def add(self, ticket: Ticket) -> Ticket: ...

    def get(self, organization_id: int, ticket_id: int) -> Ticket | None: ...

    def list(
        self,
        organization_id: int,
        label: str | None = None,
        needs_review: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Ticket]: ...

    def count(
        self,
        organization_id: int,
        label: str | None = None,
        needs_review: bool | None = None,
    ) -> int: ...


class PostgresTicketRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, ticket: Ticket) -> Ticket:
        with translated_errors(self._session):
            self._session.add(ticket)
            # commit writes the transaction durably and makes the row visible to
            # every other connection and process. The id comes from a database
            # sequence, so two processes inserting at the same moment cannot
            # receive the same id - the failure demonstrated in v1.
            self._session.commit()
        return ticket

    def get(self, organization_id: int, ticket_id: int) -> Ticket | None:
        with translated_errors(self._session):
            # session.get() can no longer be used here: it looks up by primary
            # key only, with no room for a second condition. Authorization needs
            # both, so this becomes an explicit select. Adding a boundary made a
            # convenience method unusable - normal, and part of the cost.
            #
            # Another organisation's ticket comes back as None, which the router
            # turns into 404. Deliberately not 403: a 403 would confirm that the
            # ticket exists, and existence is itself information across a tenant
            # boundary.
            return self._session.scalar(
                select(Ticket).where(
                    Ticket.id == ticket_id,
                    Ticket.organization_id == organization_id,
                )
            )

    def _filtered(
        self,
        statement,
        organization_id: int,
        label: str | None,
        needs_review: bool | None,
    ):
        """Apply the same WHERE conditions to any statement.

        list() and count() must filter identically, or the reported total would
        describe a different set of rows than the page returned. Sharing one
        function is what guarantees that, instead of two copies staying in step
        by luck.

        The organisation condition has no "if" in front of it, unlike the other
        two. There is no legitimate caller who wants every organisation's rows,
        so it is not optional.
        """
        statement = statement.where(Ticket.organization_id == organization_id)
        if label is not None:
            statement = statement.where(Ticket.label == label)
        if needs_review is not None:
            statement = statement.where(Ticket.needs_review == needs_review)
        return statement

    def list(
        self,
        organization_id: int,
        label: str | None = None,
        needs_review: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Ticket]:
        with translated_errors(self._session):
            # select() builds a SQL query. Filters become WHERE conditions, so
            # PostgreSQL does the filtering instead of Python loading every row.
            statement = self._filtered(
                select(Ticket), organization_id, label, needs_review
            )

            # ORDER BY must come before LIMIT, and must be on something stable.
            # Without a deterministic order, "the first 50 rows" is whatever the
            # database happens to return, and two pages could overlap or skip.
            # id is immutable and unique, which makes it a safe sort key.
            statement = statement.order_by(Ticket.id).limit(limit).offset(offset)

            # scalars() returns the Ticket objects rather than one-column rows.
            return list(self._session.scalars(statement))

    def count(
        self,
        organization_id: int,
        label: str | None = None,
        needs_review: bool | None = None,
    ) -> int:
        with translated_errors(self._session):
            # COUNT(*) is computed by the database; no rows are transferred.
            # It still reads every matching row internally, so it gets slower as
            # the table grows - a cost worth measuring rather than assuming.
            statement = self._filtered(
                select(func.count()).select_from(Ticket),
                organization_id,
                label,
                needs_review,
            )
            return self._session.scalar(statement) or 0