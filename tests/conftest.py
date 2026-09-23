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
        connection.execute(text("TRUNCATE TABLE tickets RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture(scope="session")
def client():
    if not Path(settings.classifier_path).exists():
        pytest.fail(
            f"Model file missing at {settings.classifier_path}. Run: python ml/train.py"
        )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def tickets_client(clean_database):
    """A client backed by an empty database.

    The app instance no longer owns storage, so a fresh app is not what makes
    tests independent - an empty database is.
    """
    if not Path(settings.classifier_path).exists():
        pytest.fail(
            f"Model file missing at {settings.classifier_path}. Run: python ml/train.py"
        )
    with TestClient(create_app()) as test_client:
        yield test_client