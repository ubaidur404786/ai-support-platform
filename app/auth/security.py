"""Password hashing and access tokens.

Two mechanisms, one purpose: proving who is calling. Nothing here touches the
database or HTTP, which is why it is easy to test on its own.
"""

from datetime import datetime, timedelta, timezone

# bcrypt hashes passwords. It is deliberately slow and salts each password
# individually, which is what makes a stolen users table expensive to attack.
import bcrypt

# PyJWT builds and verifies JSON Web Tokens: a signed payload the client sends
# back on later requests, so the server needs no session storage.
import jwt

from app.core.config import settings

# bcrypt hashes at most the first 72 BYTES and silently ignores the rest, so two
# passwords sharing a 72-byte prefix would be interchangeable. We reject longer
# input at the API boundary rather than let it be truncated quietly.
MAX_PASSWORD_BYTES = 72

# Verifying a hash takes real time by design. When the email does not exist we
# verify against this throwaway hash anyway, so a wrong email and a wrong
# password take the same time. Without it, response timing tells an attacker
# which email addresses are registered.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equalisation", bcrypt.gensalt()).decode("utf-8")


class InvalidTokenError(Exception):
    """A token was missing, malformed, expired, or signed with another key."""


def hash_password(password: str) -> str:
    """Hash a password for storage. The password itself is never stored."""
    # gensalt() creates a new random salt per password, so two users with the
    # same password get different hashes. An attacker cannot tell they match, and
    # cracking one does not crack the other.
    #
    # The salt and the cost factor are embedded in the returned string, which is
    # why verifying later needs nothing but this single column.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored hash."""
    try:
        # checkpw re-hashes using the salt inside password_hash and compares in
        # constant time. A plain == comparison would leak the answer through how
        # long it takes to find the first differing byte.
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # A corrupt or non-bcrypt value in the column. Not a crash, not a login.
        return False


def verify_password_against_dummy(password: str) -> None:
    """Spend the same time as a real check, for an email that does not exist."""
    verify_password(password, _DUMMY_HASH)


def create_access_token(user_id: int) -> str:
    """Sign a token proving this user authenticated, valid for a limited time."""
    now = datetime.now(timezone.utc)
    payload = {
        # "sub" (subject) is the standard claim for who the token is about. It
        # must be a string - some libraries reject a numeric subject.
        "sub": str(user_id),
        # "exp" is enforced by PyJWT on decode, so expiry is not our job to check.
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "iat": now,
    }
    # A JWT is SIGNED, not encrypted. Anyone holding it can read the payload;
    # they just cannot change it without the key. Never put a secret in here.
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def read_access_token(token: str) -> int:
    """Verify signature and expiry, and return the user id inside."""
    try:
        # algorithms is a list WE control. Letting the token choose its own
        # algorithm is the classic JWT vulnerability: a token claiming
        # "alg": "none" would otherwise verify with no signature at all.
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as error:
        # One error type for every reason a token is unacceptable. The caller
        # gets 401 either way, and telling them which check failed helps only an
        # attacker.
        raise InvalidTokenError(str(error)) from error

    subject = payload.get("sub")
    if subject is None:
        raise InvalidTokenError("token has no subject")
    return int(subject)
