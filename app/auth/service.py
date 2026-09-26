"""Registration and login rules.

This layer knows nothing about HTTP. It raises its own errors and the router
decides which status code each one deserves.
"""

from app.auth.models import Organization, User
from app.auth.repository import UserRepository
from app.auth.security import (
    create_access_token,
    hash_password,
    verify_password,
    verify_password_against_dummy,
)
from app.core.errors import AlreadyExistsError


class EmailAlreadyRegistered(Exception):
    """That email already has an account."""


class InvalidCredentials(Exception):
    """The email is unknown, the password is wrong, or the account is disabled.

    One error for all three cases, deliberately. "No such user" and "wrong
    password" as separate messages turn the login form into a tool for finding
    out who has an account.
    """


class AuthService:
    def __init__(self, repository: UserRepository) -> None:
        self._repository = repository

    def register(self, organization_name: str, email: str, password: str) -> User:
        """Create a user, and the organisation if it does not exist yet."""
        # Checking first gives a clean 409 in the normal case. It does NOT make
        # the operation safe on its own - see add_user's IntegrityError handler.
        if self._repository.get_user_by_email(email) is not None:
            raise EmailAlreadyRegistered(email)

        organization = self._repository.get_organization_by_name(organization_name)
        if organization is None:
            organization = self._repository.add_organization(
                Organization(name=organization_name)
            )

        user = User(
            organization_id=organization.id,
            email=email,
            # The plaintext password reaches exactly one line of this codebase
            # and is never assigned to anything that gets stored or logged.
            password_hash=hash_password(password),
            is_active=True,
        )
        try:
            return self._repository.add_user(user)
        except AlreadyExistsError as error:
            # The race we could not prevent, translated into the same error the
            # fast path raises, so the router has one case to handle.
            raise EmailAlreadyRegistered(email) from error

    def authenticate(self, email: str, password: str) -> str:
        """Check credentials and return a signed access token."""
        user = self._repository.get_user_by_email(email)

        if user is None:
            # Hash anyway. Returning immediately here would make an unknown email
            # measurably faster than a known one with a wrong password.
            verify_password_against_dummy(password)
            raise InvalidCredentials(email)

        if not verify_password(password, user.password_hash):
            raise InvalidCredentials(email)

        # Checked at login AND again on every request in step 3. Here it stops a
        # new token being issued; there it stops an existing one being used.
        if not user.is_active:
            raise InvalidCredentials(email)

        return create_access_token(user.id)
