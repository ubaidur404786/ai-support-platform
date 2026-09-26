"""API tests for registration and login.

These use anonymous_client on purpose: the auth endpoints are the only ones that
must work without a token, since they are how a caller obtains one.
"""

import pytest

from app.auth.security import (
    MAX_PASSWORD_BYTES,
    hash_password,
    read_access_token,
    verify_password,
)

PASSWORD = "a-long-enough-password"


def register(client, email="maya@acme.example", organization="Acme", password=PASSWORD):
    return client.post(
        "/auth/register",
        json={
            "organization_name": organization,
            "email": email,
            "password": password,
        },
    )


def test_register_creates_a_user(anonymous_client):
    response = register(anonymous_client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "maya@acme.example"
    assert body["organization_id"] > 0
    assert body["id"] > 0


def test_no_response_ever_contains_the_password_or_its_hash(anonymous_client):
    """The reason schemas.py exists separately from models.py.

    Asserted on the raw response text rather than the parsed body, so a hash
    leaking through a nested field or an error detail would still be caught.
    """
    created = register(anonymous_client)
    logged_in = anonymous_client.post(
        "/auth/login", json={"email": "maya@acme.example", "password": PASSWORD}
    )

    for response in (created, logged_in):
        assert "password" not in response.text
        assert "$2b$" not in response.text


def test_the_email_is_stored_lowercase(anonymous_client):
    """Otherwise "Maya@ACME.example" and "maya@acme.example" become two accounts."""
    created = register(anonymous_client, email="Maya@ACME.example")

    assert created.json()["email"] == "maya@acme.example"

    # And the lowercase form logs in, which is the behaviour that matters.
    logged_in = anonymous_client.post(
        "/auth/login", json={"email": "maya@acme.example", "password": PASSWORD}
    )
    assert logged_in.status_code == 200


def test_login_returns_a_usable_token(anonymous_client):
    created = register(anonymous_client)

    response = anonymous_client.post(
        "/auth/login", json={"email": "maya@acme.example", "password": PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in_seconds"] == 3600
    # The token identifies the user who logged in, not somebody else.
    assert read_access_token(body["access_token"]) == created.json()["id"]


def test_a_second_user_can_join_an_existing_organisation(anonymous_client):
    """Registering with a known organisation name must not create a duplicate."""
    first = register(anonymous_client, email="maya@acme.example")
    second = register(anonymous_client, email="bob@acme.example")

    assert second.status_code == 201
    assert second.json()["organization_id"] == first.json()["organization_id"]


def test_a_duplicate_email_is_a_conflict(anonymous_client):
    register(anonymous_client)

    response = register(anonymous_client)

    # 409, not 503: retrying will never succeed, so it is not a storage problem.
    assert response.status_code == 409


@pytest.mark.parametrize(
    "email, password",
    [
        ("maya@acme.example", "wrong-password-entirely"),
        ("nobody@acme.example", PASSWORD),
    ],
)
def test_bad_credentials_are_refused_identically(anonymous_client, email, password):
    """An unknown email and a wrong password must be indistinguishable.

    Different messages would turn the login form into a tool for discovering who
    has an account.
    """
    register(anonymous_client)

    response = anonymous_client.post(
        "/auth/login", json={"email": email, "password": password}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"organization_name": "Acme", "email": "maya@acme.example"},
        {"organization_name": "Acme", "email": "not-an-email", "password": PASSWORD},
        {"organization_name": "Acme", "email": "maya@acme.example", "password": "short"},
        {"organization_name": "", "email": "maya@acme.example", "password": PASSWORD},
        {
            "organization_name": "Acme",
            "email": "maya@acme.example",
            # Over bcrypt's 72-byte limit, where the excess would be silently
            # ignored rather than hashed.
            "password": "x" * (MAX_PASSWORD_BYTES + 1),
        },
    ],
)
def test_register_rejects_invalid_input(anonymous_client, payload):
    assert anonymous_client.post("/auth/register", json=payload).status_code == 422


def test_a_multibyte_password_is_measured_in_bytes_not_characters():
    """Field(max_length=...) counts characters; bcrypt counts bytes.

    A password of 40 three-byte characters is 120 bytes - well past the limit -
    while being only 40 characters long. Checked here rather than through HTTP
    because it is a property of the validator, not of the endpoint.
    """
    from pydantic import ValidationError

    from app.auth.schemas import RegisterRequest

    # 30 characters, 90 bytes.
    long_in_bytes = "é" * 30 + "é" * 10

    with pytest.raises(ValidationError):
        RegisterRequest(
            organization_name="Acme", email="maya@acme.example", password=long_in_bytes
        )


# --- Hashing, with no database and no HTTP -----------------------------------


def test_the_same_password_produces_different_hashes():
    """Because each hash carries its own random salt.

    Without per-password salts, two users with the same password would have
    identical hashes: an attacker could see that they match, and cracking one
    would crack both.
    """
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)

    assert first != second
    # Both still verify, because the salt is stored inside the hash itself.
    assert verify_password(PASSWORD, first)
    assert verify_password(PASSWORD, second)


def test_verify_rejects_a_wrong_password():
    assert not verify_password("not-the-password", hash_password(PASSWORD))


def test_verify_survives_a_corrupt_stored_hash():
    """A junk value in the column must be a failed login, not a 500."""
    assert not verify_password(PASSWORD, "this-is-not-a-bcrypt-hash")


def test_a_tampered_token_is_rejected():
    from app.auth.security import InvalidTokenError, create_access_token

    token = create_access_token(user_id=7)
    header, payload, signature = token.split(".")
    # Same payload, one character changed in the signature.
    tampered = f"{header}.{payload}.{signature[:-1]}x"

    with pytest.raises(InvalidTokenError):
        read_access_token(tampered)
