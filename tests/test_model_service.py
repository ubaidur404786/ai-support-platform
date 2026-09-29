"""Tests for the model service (v12): app/model_service/main.py.

The real 1.1 GB model is replaced by FakeLlama, which answers like llama.cpp's
create_chat_completion() but instantly. So these tests check OUR code: the
HTTP endpoints, the "not loaded" and "busy" answers, and that every queue
place is given back - after an answer, a stream, and a crash.
"""

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.model_service import main as model_service
from app.model_service import model

MESSAGES = [{"role": "user", "content": "How long does a refund take?"}]


class FakeLlama:
    """Stands in for llama_cpp.Llama. Can be told to wait, to test the queue."""

    def __init__(self, pieces=("Refunds take ", "ten business days.")):
        self.pieces = list(pieces)
        self.calls = []
        self.crash_after_first_piece = False
        self.wait_for = None  # a threading.Event to block on, or None

    def create_chat_completion(self, messages, max_tokens, temperature, stream=False):
        self.calls.append(messages)
        if self.wait_for is not None:
            self.wait_for.wait(timeout=10)
        if not stream:
            return {"choices": [{"message": {"content": "".join(self.pieces)}}]}
        return self._chunks()

    def _chunks(self):
        for piece in self.pieces:
            yield {"choices": [{"delta": {"content": piece}}]}
            if self.crash_after_first_piece:
                raise RuntimeError("out of memory")


@pytest.fixture
def fake_llama(monkeypatch):
    llama = FakeLlama()
    # Pretend load() already ran. monkeypatch puts the old value back afterwards.
    monkeypatch.setattr(model, "_model", llama)
    return llama


@pytest.fixture
def service_client():
    # No "with": the lifespan (which loads the real model file) does not run.
    return TestClient(model_service.app)


def generate(client, path="/generate"):
    return client.post(path, json={"messages": MESSAGES, "max_tokens": 50})


def test_generate_returns_the_whole_reply(service_client, fake_llama):
    response = generate(service_client)

    assert response.status_code == 200
    assert response.json() == {"text": "Refunds take ten business days."}
    assert fake_llama.calls == [MESSAGES]


def test_stream_returns_one_line_per_piece(service_client, fake_llama):
    response = generate(service_client, "/generate/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in response.text.splitlines()]
    assert lines == [{"text": "Refunds take "}, {"text": "ten business days."}]


def test_every_queue_place_is_given_back(service_client, fake_llama):
    """A place that is never returned is capacity lost until the next restart."""
    generate(service_client)
    generate(service_client, "/generate/stream")
    fake_llama.crash_after_first_piece = True
    generate(service_client, "/generate/stream")

    assert service_client.get("/health").json()["in_queue"] == 0


def test_a_crash_mid_stream_ends_with_an_error_line(service_client, fake_llama):
    fake_llama.crash_after_first_piece = True

    lines = [json.loads(line) for line in generate(service_client, "/generate/stream").text.splitlines()]

    assert lines == [{"text": "Refunds take "}, {"error": "Generation failed"}]


def test_without_a_model_both_endpoints_are_503_and_health_says_so(service_client, monkeypatch):
    monkeypatch.setattr(model, "_model", None)

    assert generate(service_client).status_code == 503
    assert generate(service_client, "/generate/stream").status_code == 503
    health = service_client.get("/health").json()
    assert health["status"] == "degraded"
    assert health["model_loaded"] is False


def test_a_full_queue_is_refused_at_once_with_retry_after(service_client, fake_llama, monkeypatch):
    """One place. The first request takes it and is held inside the model; the
    second finds the queue full and gets 503 immediately, without reaching the
    model. When the first finishes, the place is free again."""
    monkeypatch.setattr(settings, "model_max_queue", 1)
    fake_llama.wait_for = threading.Event()
    first = {}
    worker = threading.Thread(target=lambda: first.update(response=generate(service_client)))
    worker.start()
    while service_client.get("/health").json()["in_queue"] < 1:
        time.sleep(0.01)

    start = time.perf_counter()
    refused = generate(service_client)
    refused_after = time.perf_counter() - start
    stream_refused = generate(service_client, "/generate/stream")

    fake_llama.wait_for.set()
    worker.join()

    assert refused.status_code == 503
    assert refused.headers["Retry-After"] == str(settings.model_busy_retry_after_seconds)
    assert refused_after < 1  # refused at once, not after waiting its turn
    assert stream_refused.status_code == 503
    assert len(fake_llama.calls) == 1  # only the first request reached the model
    assert first["response"].status_code == 200
    assert generate(service_client).status_code == 200  # the place is free again


def test_requests_are_validated(service_client, fake_llama):
    assert service_client.post("/generate", json={"messages": [], "max_tokens": 50}).status_code == 422
    assert service_client.post("/generate", json={"messages": MESSAGES, "max_tokens": 0}).status_code == 422
    assert service_client.post("/generate", json={"messages": MESSAGES, "max_tokens": 100_000}).status_code == 422
    assert fake_llama.calls == []



def test_only_the_model_service_uses_llama_cpp():
    """The point of v12: no API module can load the 1.1 GB model.

    Until v11, app/answers/generator.py imported llama_cpp (lazily, on the
    first answer), so every API process could end up holding a copy. Now only
    app/model_service/ may mention it. A source check, because an import
    check would pass in v11 too: that import only happened on the first answer.
    """
    app_folder = Path(model.__file__).parents[1]
    users = [
        path.relative_to(app_folder).as_posix()
        for path in app_folder.rglob("*.py")
        if "llama_cpp" in path.read_text(encoding="utf-8")
    ]

    assert users == ["model_service/model.py"]
