"""Tests for the embedding service (v13) and the client the API and worker use.

These use the real embedding model: it is small (~90 MB) and loaded once per
test run by the embedding_service fixture in conftest.py.
"""

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.core.config import settings
from app.documents import embeddings
from app.documents.embeddings import EmbeddingUnavailable, embed_texts
from app.embedding_service import model


def embed(service, texts, model_name=settings.embedding_model):
    return service.post("/embed", json={"texts": texts, "model": model_name})


def test_health_says_the_model_is_loaded(embedding_service):
    body = embedding_service.get("/health").json()

    assert body["status"] == "ok"
    assert body["model_loaded"] is True


def test_vectors_have_384_numbers_and_length_one(embedding_service):
    response = embed(embedding_service, ["How do I reset my password?"])

    assert response.status_code == 200
    vector = np.array(response.json()["vectors"][0])
    assert vector.shape == (384,)
    assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-3)


def test_a_paraphrase_is_closer_than_an_unrelated_sentence(embedding_service):
    question, paraphrase, unrelated = embed(
        embedding_service,
        [
            "Can I get my money back?",
            "Refunds are issued within ten business days.",
            "Our office has a new coffee machine.",
        ],
    ).json()["vectors"]

    assert np.dot(question, paraphrase) > np.dot(question, unrelated)


def test_asking_for_another_model_is_refused(embedding_service):
    """Vectors from two models cannot be compared, so the service says no
    instead of quietly returning vectors from the model it has."""
    response = embed(embedding_service, ["hello"], model_name="some/other-model")

    assert response.status_code == 400


def test_too_many_texts_in_one_request_is_refused(embedding_service):
    response = embed(embedding_service, ["hello"] * 65)

    assert response.status_code == 422


def test_the_client_splits_long_lists_into_several_requests(monkeypatch):
    sizes = []

    def handler(request):
        texts = json.loads(request.content)["texts"]
        sizes.append(len(texts))
        return httpx.Response(200, json={"vectors": [[0.0] * 384 for _ in texts]})

    client = httpx.Client(base_url="http://embedding-service", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(embeddings, "_client", client)

    vectors = embed_texts([f"text number {n}" for n in range(150)])

    assert sizes == [64, 64, 22]
    assert vectors.shape == (150, 384)


def test_a_service_that_is_down_raises_embedding_unavailable(monkeypatch):
    def not_running(request):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(base_url="http://embedding-service", transport=httpx.MockTransport(not_running))
    monkeypatch.setattr(embeddings, "_client", client)

    with pytest.raises(EmbeddingUnavailable):
        embed_texts(["hello"])


def test_semantic_search_is_a_503_when_the_service_is_down(tickets_client, monkeypatch):
    def not_running(request):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(base_url="http://embedding-service", transport=httpx.MockTransport(not_running))
    monkeypatch.setattr(embeddings, "_client", client)

    response = tickets_client.get("/documents/search", params={"q": "refund", "mode": "semantic"})

    assert response.status_code == 503
    # Keyword search does not need the service and keeps working.
    assert tickets_client.get("/documents/search", params={"q": "refund"}).status_code == 200


def test_only_the_embedding_service_loads_the_model():
    """The point of v13: the API and the worker no longer load the model.

    A source check, like the one for llama_cpp in v12: only
    app/embedding_service/model.py may mention fastembed.
    """
    app_folder = Path(model.__file__).parents[1]
    users = [
        path.relative_to(app_folder).as_posix()
        for path in app_folder.rglob("*.py")
        if "fastembed" in path.read_text(encoding="utf-8")
    ]

    assert users == ["embedding_service/model.py"]
