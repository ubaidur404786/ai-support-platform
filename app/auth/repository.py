"""Storage for organisations and users."""

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.models import Organization, User
from app.core.errors import AlreadyExistsError, StorageError, translated_errors


class UserRepository(Protocol):
    """What the auth service needs from storage.

    Organisations and users live in one repository because registration creates
    both in one operation. Splitting them would mean two transaction boundaries
    for something that must succeed or fail as a unit.
    """

    def get_user_by_email(self, email: str) -> User | None: ...

    def get_user(self, user_id: int) -> User | None: ...

    def add_user(self, user: User) -> User: ...

    def get_organization_by_name(self, name: str) -> Organization | None: ...

    def add_organization(self, organization: Organization) -> Organization: ...


class PostgresUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user_by_email(self, email: str) -> User | None:
        with translated_errors(self._session):
            # The unique index on email makes this a direct index lookup rather
            # than a scan, which matters because it runs on every login.
            return self._session.scalar(select(User).where(User.email == email))

    def get_user(self, user_id: int) -> User | None:
        with translated_errors(self._session):
            return self._session.get(User, user_id)

    def add_user(self, user: User) -> User:
        try:
            self._session.add(user)
            self._session.commit()
        except IntegrityError as error:
            # IntegrityError is a SUBCLASS of SQLAlchemyError, so it must be
            # caught first or the broader handler below would swallow it.
            #
            # This is the race the service's "does this email exist?" check
            # cannot close: two registrations can both look, both find nothing,
            # and both insert. Only the unique index sees both at once.
            self._session.rollback()
            raise AlreadyExistsError(str(error)) from error
        except SQLAlchemyError as error:
            self._session.rollback()
            raise StorageError(str(error)) from error
        return user

    def get_organization_by_name(self, name: str) -> Organization | None:
        with translated_errors(self._session):
            return self._session.scalar(
                select(Organization).where(Organization.name == name)
            )

    def add_organization(self, organization: Organization) -> Organization:
        try:
            self._session.add(organization)
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise AlreadyExistsError(str(error)) from error
        except SQLAlchemyError as error:
            self._session.rollback()
            raise StorageError(str(error)) from error
        return organization
