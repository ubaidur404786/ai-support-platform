"""What the auth endpoints accept and return.

Separate from models.py on purpose. The User model has a password_hash column;
no response schema here has such a field, so it cannot leak by accident. That
separation is the whole reason the two files exist.
"""

from datetime import datetime

# EmailStr validates the address format and is backed by email-validator.
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.auth.security import MAX_PASSWORD_BYTES


class RegisterRequest(BaseModel):
    organization_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    # A minimum length is the one password rule with real evidence behind it.
    # Forced symbols and digits mostly produce "Password1!".
    password: str = Field(min_length=12)

    @field_validator("email")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        # Stored lowercase so "Maya@x.com" and "maya@x.com" are one account.
        # Normalising on the way IN means every later lookup can be a plain
        # equality match on an indexed column.
        return value.lower()

    @field_validator("password")
    @classmethod
    def _within_bcrypt_limit(cls, value: str) -> str:
        # Field(max_length=...) counts CHARACTERS; bcrypt counts BYTES. A password
        # of 72 accented characters is well over 72 bytes, so the character limit
        # would let silently-truncated input through.
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    # No min_length here. Login must accept whatever is already stored, and
    # rejecting a short password before checking it would reveal the rule.
    password: str

    @field_validator("email")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return value.lower()


class UserResponse(BaseModel):
    id: int
    email: str
    organization_id: int
    created_at: datetime

    # from_attributes lets FastAPI build this from the ORM object's attributes
    # rather than a dict. Only the fields listed above are read, so
    # password_hash never reaches a response.
    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    # "bearer" means "whoever bears this token is treated as the user". That is
    # exactly why the token must travel over HTTPS in any real deployment.
    token_type: str = "bearer"
    expires_in_seconds: int
