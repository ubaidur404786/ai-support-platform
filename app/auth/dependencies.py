"""Wiring for the auth module."""

from fastapi import Depends, HTTPException, status

# HTTPBearer reads the "Authorization: Bearer <token>" header and adds a token
# box to the generated /docs page.
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.repository import PostgresUserRepository, UserRepository
from app.auth.security import InvalidTokenError, read_access_token
from app.auth.service import AuthService
from app.core.database import get_session
from app.core.errors import StorageError


def get_user_repository(session: Session = Depends(get_session)) -> UserRepository:
    return PostgresUserRepository(session)


def get_auth_service(
    repository: UserRepository = Depends(get_user_repository),
) -> AuthService:
    return AuthService(repository=repository)


# auto_error=False on purpose. With the default, a missing header produces 403,
# which is the wrong meaning: 403 is "I know who you are and the answer is no".
# No header at all is 401, so we raise it ourselves.
_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    repository: UserRepository = Depends(get_user_repository),
) -> User:
    """Resolve the caller from their token, or refuse the request.

    A dependency, so it runs BEFORE the endpoint body. An endpoint that declares
    it cannot execute for an anonymous caller - the refusal is structural rather
    than a check the handler might forget to write.
    """
    if credentials is None:
        raise _unauthorized("Not authenticated")

    try:
        user_id = read_access_token(credentials.credentials)
    except InvalidTokenError:
        # Expired, tampered with, signed by another key: one answer for all of
        # them. Saying which check failed helps only an attacker.
        raise _unauthorized("Invalid or expired token")

    try:
        user = repository.get_user(user_id)
    except StorageError:
        # Not 401. We could not determine who they are, which is a server
        # problem, not a credentials problem.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is unavailable",
        )

    # This lookup is what the token alone cannot give us: a user deactivated one
    # minute ago still holds a validly signed token for up to an hour.
    if user is None or not user.is_active:
        raise _unauthorized("Invalid or expired token")

    return user
