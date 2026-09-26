"""Errors that cross module boundaries.

StorageError started inside the tickets module. Once a second module needed the
same idea, keeping it there would have made auth depend on tickets for no reason.
A shared concern belongs in core.
"""

from contextlib import contextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


class StorageError(RuntimeError):
    """Storage is unavailable or a query failed.

    Services and routers must not depend on SQLAlchemy's exception types, or
    swapping the database would change code in every layer. Database errors are
    translated here into one error the upper layers own.
    """


class AlreadyExistsError(RuntimeError):
    """A write violated a uniqueness rule.

    Deliberately NOT a subclass of StorageError. The router turns StorageError
    into 503 "try again later", and a duplicate email is not a temporary
    condition - retrying will never help. Different cause, different HTTP status,
    so different type.
    """


@contextmanager
def translated_errors(session: Session):
    """Turn any SQLAlchemy failure into StorageError, leaving no open transaction.

    A failed statement leaves the session broken until it is rolled back; the
    next query on it would fail for the wrong reason and hide the real cause.
    """
    try:
        yield
    except SQLAlchemyError as error:
        session.rollback()
        raise StorageError(str(error)) from error
