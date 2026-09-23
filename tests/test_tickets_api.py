"""HTTP tests for the tickets endpoints."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_create_ticket_classifies_and_stores(tickets_client):
    response = tickets_client.post(
        "/tickets", json={"text": "I was charged twice for my subscription"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 1
    assert body["label"] == "billing"
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["model_version"] == "v0.1.0"
    assert body["needs_review"] is False
    assert body["created_at"]


def test_ids_increment(tickets_client):
    first = tickets_client.post("/tickets", json={"text": "I cannot log in"}).json()
    second = tickets_client.post("/tickets", json={"text": "Please add dark mode"}).json()

    assert (first["id"], second["id"]) == (1, 2)


def test_low_confidence_is_flagged_for_review(tickets_client):
    # "hello" is not a support ticket, but the model must still pick a label.
    # The confidence threshold is what makes that uncertainty visible.
    body = tickets_client.post("/tickets", json={"text": "hello"}).json()

    assert body["needs_review"] is True
    assert body["confidence"] < 0.55


def test_get_ticket_returns_what_was_stored(tickets_client):
    created = tickets_client.post(
        "/tickets", json={"text": "The export button does nothing"}
    ).json()

    response = tickets_client.get(f"/tickets/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_unknown_ticket_returns_404(tickets_client):
    response = tickets_client.get("/tickets/999")

    assert response.status_code == 404
    assert "999" in response.json()["detail"]


def test_get_ticket_with_non_numeric_id_returns_422(tickets_client):
    response = tickets_client.get("/tickets/abc")

    assert response.status_code == 422


def test_list_is_empty_for_a_new_app(tickets_client):
    body = tickets_client.get("/tickets").json()

    assert body == {"total": 0, "items": []}


def test_list_filters_by_label_and_review_flag(tickets_client):
    tickets_client.post("/tickets", json={"text": "I need a refund for my payment"})
    tickets_client.post("/tickets", json={"text": "Please add a dark mode option"})
    tickets_client.post("/tickets", json={"text": "hello"})

    all_tickets = tickets_client.get("/tickets").json()
    billing = tickets_client.get("/tickets", params={"label": "billing"}).json()
    review = tickets_client.get("/tickets", params={"needs_review": "true"}).json()

    assert all_tickets["total"] == 3
    assert billing["total"] == 1
    assert billing["items"][0]["label"] == "billing"
    assert review["total"] == 1
    assert review["items"][0]["needs_review"] is True


def test_list_with_unknown_label_returns_empty(tickets_client):
    tickets_client.post("/tickets", json={"text": "I need a refund for my payment"})

    body = tickets_client.get("/tickets", params={"label": "does_not_exist"}).json()

    assert body == {"total": 0, "items": []}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": ""},
        {"text": "   "},
        {"text": "ab"},
        {"text": "x" * 5001},
        {"text": 123},
    ],
)
def test_create_rejects_invalid_input(tickets_client, payload):
    assert tickets_client.post("/tickets", json=payload).status_code == 422


def test_create_returns_503_when_model_missing():
    # Tickets depend on the classifier, so a missing model must fail the same
    # controlled way /classify does - not with a crash.
    broken_app = create_app(Settings(classifier_path="models/does_not_exist.joblib"))

    with TestClient(broken_app) as broken_client:
        response = broken_client.post("/tickets", json={"text": "I cannot log in"})

        assert response.status_code == 503
        assert response.json()["detail"] == "Model is not loaded"


def test_tickets_do_not_survive_a_restart(tickets_client):
    """The in-memory store is per-process. This documents that limitation."""
    tickets_client.post("/tickets", json={"text": "I need a refund for my payment"})
    assert tickets_client.get("/tickets").json()["total"] == 1

    # A new app instance is the closest thing to restarting the server.
    with TestClient(create_app()) as restarted:
        assert restarted.get("/tickets").json()["total"] == 0