"""v14: the API's /health also reports the two model services, and the API
refuses to start without a real signing key."""

import httpx
import pytest

from app.answers import generator
from app.core.config import Settings
from app.main import create_app


def down(request):
    raise httpx.ConnectError("connection refused")


def test_health_reports_both_services(client):
    body = client.get("/health").json()

    # In the tests the embedding service runs in memory (conftest.py) and the
    # model service is not running at all.
    assert body["embedding_service"] == "ok"
    assert body["model_service"] == "down"


def test_a_service_that_is_down_does_not_make_the_api_unhealthy(client, monkeypatch):
    """The API still logs people in, stores tickets and runs keyword search
    without the model service. If "status" followed the model service, a
    Kubernetes readiness check would take every API copy out of service
    whenever the model service restarts."""
    monkeypatch.setattr(generator, "_client", httpx.Client(base_url="http://model", transport=httpx.MockTransport(down)))

    body = client.get("/health").json()

    assert body["model_service"] == "down"
    assert body["status"] == "ok"


def test_a_service_without_its_model_is_degraded(client, monkeypatch):
    def no_model(request):
        return httpx.Response(200, json={"status": "degraded", "model_loaded": False})

    monkeypatch.setattr(generator, "_client", httpx.Client(base_url="http://model", transport=httpx.MockTransport(no_model)))

    assert client.get("/health").json()["model_service"] == "degraded"


@pytest.mark.parametrize("key", ["", "too-short"])
def test_the_api_refuses_to_start_without_a_real_signing_key(key):
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        create_app(Settings(jwt_secret_key=key))
