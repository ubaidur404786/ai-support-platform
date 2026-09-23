"""Shared pytest fixtures.

A fixture is a reusable piece of setup. pytest creates it and passes it to any
test that names it as a parameter.
"""

from pathlib import Path

import pytest

# TestClient sends requests to the app in-process, without starting a real server.
from fastapi.testclient import TestClient
from app.main import app, create_app
from app.core.config import settings



@pytest.fixture(scope="session")
def client():
    if not Path(settings.classifier_path).exists():
        pytest.fail(
            f"Model file missing at {settings.classifier_path}. Run: python ml/train.py"
        )
    # Using "with" runs the lifespan, so the model is loaded exactly as in production.
    # scope="session" means this happens once for the whole test run, not per test.
    with TestClient(app) as test_client:
        yield test_client
        


@pytest.fixture
def tickets_client():
    """A fresh app (and therefore a fresh, empty ticket store) for each test.

    The session-scoped `client` is shared, so tickets created in one test would
    leak into the next. Building a new app per test keeps them independent.
    """
    if not Path(settings.classifier_path).exists():
        pytest.fail(
            f"Model file missing at {settings.classifier_path}. Run: python ml/train.py"
        )
    with TestClient(create_app()) as test_client:
        yield test_client