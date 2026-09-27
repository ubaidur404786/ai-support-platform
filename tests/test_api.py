"""HTTP-level tests: correct responses, invalid input, and failure modes."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import _register_and_login


def test_health_reports_model_loaded(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["database_reachable"] is True
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["model_version"] == "v0.1.0"


# /classify requires a token from v5, so these tests use the authenticated
# tickets_client. The session-wide `client` stays anonymous and now only checks
# health - and that /classify refuses it.


def test_classify_requires_a_token(client):
    response = client.post("/classify", json={"text": "I was charged twice"})

    assert response.status_code == 401


def test_classify_returns_label_and_confidence(tickets_client):
    response = tickets_client.post(
        "/classify", json={"text": "I was charged twice for my subscription"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "billing"
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["model_version"] == "v0.1.0"


# parametrize runs the same test once per listed payload.
@pytest.mark.parametrize(
    "payload",
    [
        {},                    # missing field
        {"text": ""},          # empty
        {"text": "   "},       # blank (caught by our validator)
        {"text": "ab"},        # shorter than min_length
        {"text": "x" * 2001},  # longer than max_length
        {"text": 123},         # wrong type
    ],
)
def test_classify_rejects_invalid_input(tickets_client, payload):
    response = tickets_client.post("/classify", json=payload)

    # 422 = "unprocessable": the request was understood but its content is invalid.
    assert response.status_code == 422


def test_classify_returns_503_when_model_missing(clean_database):
    # A separate app with a broken path, so the shared client is not affected.
    broken_app = create_app(Settings(classifier_path="models/does_not_exist.joblib"))

    with TestClient(broken_app) as broken_client:
        health = broken_client.get("/health").json()
        assert health["model_loaded"] is False
        assert health["model_version"] is None

        # Login does not need the model, so a user can still get a token.
        token = _register_and_login(broken_client, "Acme", "user@acme.example")
        broken_client.headers.update({"Authorization": f"Bearer {token}"})
        response = broken_client.post("/classify", json={"text": "I cannot log in"})
        assert response.status_code == 503
        assert response.json()["detail"] == "Model is not loaded"


def test_classify_returns_500_when_prediction_crashes(tickets_client, monkeypatch):
    def explode(text):
        raise RuntimeError("simulated model failure")

    # tickets_client.app is the app instance behind the client.
    monkeypatch.setattr(tickets_client.app.state.classifier, "predict", explode)

    response = tickets_client.post("/classify", json={"text": "The app crashes on upload"})

    assert response.status_code == 500
    # The client gets a generic message; the real error goes to the log only.
    assert response.json()["detail"] == "Prediction failed"

def test_health_reports_database_unreachable(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.health import router as health_router

    def broken_connect():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(health_router.engine, "connect", broken_connect)

    body = client.get("/health").json()

    # Still HTTP 200: health reports problems, it does not become one.
    assert body["database_reachable"] is False
    assert body["status"] == "degraded"