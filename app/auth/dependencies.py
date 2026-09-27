"""Wiring for the auth module."""

from fastapi import Depends, HTTPException, Request, status

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
from app.core.rate_limit import enforce


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


def limit_auth_attempts(request: Request) -> None:
    """Budget for /auth/login and /auth/register, per client address.

    Keyed on the address because the caller has no identity yet - that is what
    they are trying to obtain. Runs before the handler, so a refused attempt
    never reaches bcrypt: a 429 costs microseconds, a login ~680 ms.

    request.client.host is the address of whoever opened the TCP connection.
    Behind a reverse proxy that would be the proxy, and every user would share
    one bucket; the fix then is to trust the proxy's X-Forwarded-For header, and
    only the proxy's. There is no proxy yet, so the header is deliberately
    ignored - trusting it now would let any caller claim a new address per
    request and escape the limit entirely.
    """
    address = request.client.host if request.client else "unknown"
    enforce(request, "auth", address)


def limit_inference(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    """Budget for model inference, per user.

    Keyed on the user, not the address: a user cannot escape it by changing
    networks, and colleagues behind one office NAT do not share a budget.
    Declaring get_current_user here does not authenticate twice - FastAPI runs a
    dependency once per request and reuses the result.
    """
    enforce(request, "inference", f"user:{current_user.id}")


def limit_ingestion(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    """Budget for document uploads, per user.

    Separate from inference: an upload costs extraction and chunking rather than
    model time, and one budget for both would let a burst of uploads lock a user
    out of classifying tickets.
    """
    enforce(request, "ingestion", f"user:{current_user.id}")
