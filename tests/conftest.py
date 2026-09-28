"""Shared pytest fixtures.

A fixture is a reusable piece of setup. pytest creates it and passes it to any
test that names it as a parameter.
"""

import os
from pathlib import Path

# Point the application at the test database BEFORE importing anything from app.
# app.core.config builds its Settings object at import time, and
# app.core.database builds the engine from those settings, so this must run
# first. That ordering requirement is a smell caused by the module-level engine;
# the cleaner fix is to build the engine inside create_app, which is a refactor
# for a later version.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://support:support@localhost:5432/support_platform_test",
)

# The application refuses to start without a signing key, so tests must supply
# one. setdefault, not assignment: a key already in the environment wins.
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-only-signing-key-never-used-outside-the-test-suite"
)

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.main import app, create_app


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Build the test database schema by running the real migrations.

    autouse=True means every test gets this without asking for it. Using Alembic
    rather than create_all means a model change with no matching migration makes
    the tests fail, which is what we want.
    """
    if "test" not in settings.database_url:
        pytest.fail(
            f"Refusing to run migrations against {settings.database_url!r}: "
            "tests truncate tables and this does not look like a test database."
        )
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def clean_database():
    """Empty the tables and reset id sequences before a test runs.

    TRUNCATE is much faster than DELETE for clearing a whole table, and
    RESTART IDENTITY sets sequences back to 1 so ids are predictable.
    """
    with engine.begin() as connection:
        # One statement for every table. TRUNCATE on tickets alone would
        # fail now that a foreign key points at organizations, and listing them
        # together lets PostgreSQL drop the constraint check for the duration.
        connection.execute(
            text(
                "TRUNCATE TABLE document_files, document_chunks, documents, tickets, users, organizations "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


def _require_model() -> None:
    if not Path(settings.classifier_path).exists():
        pytest.fail(
            f"Model file missing at {settings.classifier_path}. Run: python ml/train.py"
        )


def _register_and_login(test_client: TestClient, organization: str, email: str) -> str:
    """Create an organisation with one user and return their access token.

    Registration goes through the real HTTP endpoints rather than inserting rows
    directly. It is slower, but it means the fixture exercises the same path a
    real client takes - a test setup that bypasses the API can pass while the API
    itself is broken.
    """
    password = "fixture-password-long-enough"
    created = test_client.post(
        "/auth/register",
        json={
            "organization_name": organization,
            "email": email,
            "password": password,
        },
    )
    assert created.status_code == 201, created.text

    logged_in = test_client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    assert logged_in.status_code == 200, logged_in.text
    return logged_in.json()["access_token"]


@pytest.fixture(scope="session")
def client():
    """The shared app instance, unauthenticated.

    Used by the health and classification tests, which stay public in v4.
    """
    _require_model()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def anonymous_client(clean_database):
    """A client with no token, for the tests that assert 401.

    Needed as its own fixture now that tickets_client carries credentials: with
    only an authenticated client, nothing would ever exercise the refusal path.
    """
    _require_model()
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def tickets_client(clean_database):
    """A client backed by an empty database, authenticated as Acme's user.

    The app instance no longer owns storage, so a fresh app is not what makes
    tests independent - an empty database is.

    This fixture keeps its name from v3 even though its meaning widened: "a
    client that can use the tickets API" now includes holding a token. Renaming
    it would have touched 68 call sites for no behavioural gain.
    """
    _require_model()
    with TestClient(create_app()) as test_client:
        token = _register_and_login(test_client, "Acme", "user@acme.example")
        # headers set on the client are sent with every later request from it.
        test_client.headers.update({"Authorization": f"Bearer {token}"})
        yield test_client


@pytest.fixture
def second_org_client(tickets_client):
    """A client for a DIFFERENT organisation, sharing the same database.

    Depends on tickets_client so both exist against one clean_database. This is
    the only fixture that can prove tenant isolation: one organisation writes, the
    other must not be able to read.
    """
    _require_model()
    with TestClient(create_app()) as test_client:
        token = _register_and_login(test_client, "Globex", "user@globex.example")
        test_client.headers.update({"Authorization": f"Bearer {token}"})
        yield test_client