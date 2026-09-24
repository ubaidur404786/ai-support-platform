"""HTTP tests for the tickets endpoints."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.classification.dependencies import get_classifier
from app.core.config import settings
from app.main import create_app
from app.tickets.dependencies import get_repository
from app.tickets.repository import StorageError


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

    assert body["total"] == 0
    assert body["items"] == []


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

    assert body["total"] == 0
    assert body["items"] == []


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


def test_create_returns_503_when_model_missing(tickets_client):
    # dependency_overrides replaces a dependency for testing. It is the reason
    # endpoints ask for what they need instead of fetching it themselves.
    def no_model():
        raise HTTPException(status_code=503, detail="Model is not loaded")

    tickets_client.app.dependency_overrides[get_classifier] = no_model
    try:
        response = tickets_client.post("/tickets", json={"text": "I cannot log in"})
        assert response.status_code == 503
        assert response.json()["detail"] == "Model is not loaded"
    finally:
        tickets_client.app.dependency_overrides.clear()


def test_endpoints_return_503_when_storage_fails(tickets_client):
    class BrokenRepository:
        def add(self, ticket):
            raise StorageError("connection refused")

        def get(self, ticket_id):
            raise StorageError("connection refused")

        def list(self, label=None, needs_review=None, limit=50, offset=0):
            raise StorageError("connection refused")

        def count(self, label=None, needs_review=None):
            raise StorageError("connection refused")

    tickets_client.app.dependency_overrides[get_repository] = BrokenRepository
    try:
        assert tickets_client.post("/tickets", json={"text": "I cannot log in"}).status_code == 503
        assert tickets_client.get("/tickets/1").status_code == 503
        assert tickets_client.get("/tickets").status_code == 503
    finally:
        tickets_client.app.dependency_overrides.clear()

def test_tickets_survive_a_restart(tickets_client):
    """What v1 could not do: state outlives the process that created it."""
    tickets_client.post("/tickets", json={"text": "I need a refund for my payment"})

    # A new app instance is the closest thing to restarting the server.
    with TestClient(create_app()) as restarted:
        body = restarted.get("/tickets").json()

    assert body["total"] == 1
    assert body["items"][0]["text"] == "I need a refund for my payment"

def test_two_app_instances_share_one_database(tickets_client):
    """In v1 these were two separate worlds; now they are one."""
    created = tickets_client.post("/tickets", json={"text": "I cannot log in"}).json()

    with TestClient(create_app()) as other_instance:
        fetched = other_instance.get(f"/tickets/{created['id']}").json()

    assert fetched == created


def test_ids_come_from_the_database_sequence(tickets_client):
    """Ids are assigned by PostgreSQL, not by any application process.

    This is what makes duplicate ids impossible across replicas - and it is why
    the service must not set id itself.
    """
    ids = [
        tickets_client.post("/tickets", json={"text": f"ticket number {i}"}).json()["id"]
        for i in range(1, 4)
    ]

    assert ids == [1, 2, 3]

# --- Pagination -------------------------------------------------------------


def _create_tickets(client, count: int) -> None:
    for i in range(count):
        client.post("/tickets", json={"text": f"I need a refund for payment {i}"})


def test_list_applies_a_default_page_size(tickets_client):
    """A client that asks for nothing must not receive everything."""
    _create_tickets(tickets_client, 3)

    body = tickets_client.get("/tickets").json()

    assert body["limit"] == settings.default_page_size
    assert body["offset"] == 0


def test_limit_controls_how_many_are_returned(tickets_client):
    _create_tickets(tickets_client, 5)

    body = tickets_client.get("/tickets", params={"limit": 2}).json()

    assert len(body["items"]) == 2
    # total describes every matching ticket, not the page.
    assert body["total"] == 5


def test_offset_moves_the_window(tickets_client):
    _create_tickets(tickets_client, 5)

    first = tickets_client.get("/tickets", params={"limit": 2, "offset": 0}).json()
    second = tickets_client.get("/tickets", params={"limit": 2, "offset": 2}).json()

    assert [t["id"] for t in first["items"]] == [1, 2]
    assert [t["id"] for t in second["items"]] == [3, 4]


def test_pages_cover_every_ticket_exactly_once(tickets_client):
    _create_tickets(tickets_client, 7)

    seen: list[int] = []
    offset = 0
    while True:
        body = tickets_client.get("/tickets", params={"limit": 3, "offset": offset}).json()
        if not body["items"]:
            break
        seen.extend(t["id"] for t in body["items"])
        offset += 3

    # No duplicates and no gaps - the property that makes paging usable.
    assert seen == [1, 2, 3, 4, 5, 6, 7]


def test_offset_past_the_end_returns_an_empty_page(tickets_client):
    _create_tickets(tickets_client, 2)

    body = tickets_client.get("/tickets", params={"offset": 500}).json()

    # Empty, not an error: the client asked a valid question about a page that
    # happens to hold nothing. total still reports what exists.
    assert body["items"] == []
    assert body["total"] == 2


def test_limit_above_the_maximum_is_rejected(tickets_client):
    response = tickets_client.get(
        "/tickets", params={"limit": settings.max_page_size + 1}
    )

    # 422, not a silently clamped page: the client is told its request was
    # invalid instead of quietly receiving something it did not ask for.
    assert response.status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": 10000},
        {"offset": -1},
        {"limit": "abc"},
        {"offset": "abc"},
    ],
)
def test_invalid_pagination_parameters_are_rejected(tickets_client, params):
    assert tickets_client.get("/tickets", params=params).status_code == 422


def test_pagination_combines_with_filters(tickets_client):
    _create_tickets(tickets_client, 4)          # all billing
    tickets_client.post("/tickets", json={"text": "hello"})  # low confidence

    body = tickets_client.get(
        "/tickets", params={"label": "billing", "limit": 2}
    ).json()

    assert len(body["items"]) == 2
    # total must count only the filtered rows, or the client cannot know how
    # many pages to request.
    assert body["total"] == 4
    assert all(t["label"] == "billing" for t in body["items"])
