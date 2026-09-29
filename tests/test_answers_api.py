"""Tests for POST /answers (v10): retrieve, check, generate.

Most tests replace the language model with a small fake function. The fake
records what it was sent and returns a fixed reply, so each test can check the
parts that are OUR code: which chunks reach the model, when the model is not
called at all, and how its reply is turned into a response. The real model is
exercised by one test at the end and by scripts/evaluate_rag.py.
"""

import json
from pathlib import Path

import pytest

from app.answers.generator import GenerationUnavailable
from app.core.config import settings
from tests.test_documents_api import PASSWORDS, REFUNDS, upload_and_process


class FakeModel:
    def __init__(self, reply="Refunds take up to ten business days [1]."):
        self.reply = reply
        self.calls = []  # every list of messages it was sent

    def __call__(self, messages, max_tokens):
        self.calls.append(messages)
        return self.reply

    def prompt(self) -> str:
        """The user message of the last call: the sources and the question."""
        return self.calls[-1][-1]["content"]


@pytest.fixture
def fake_model(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr("app.answers.service.generate", model)
    return model


def ask(client, question):
    return client.post("/answers", json={"question": question})


def test_an_answer_is_written_from_the_retrieved_chunks(tickets_client, fake_model):
    refunds = upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    upload_and_process(tickets_client, "passwords.md", PASSWORDS.encode())

    response = ask(tickets_client, "Can I get my money back?")

    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is True
    assert body["reason"] == "answered"
    assert body["answer"] == fake_model.reply
    # The refund chunk is the first source, and the model was shown its text.
    assert body["sources"][0]["document_id"] == refunds["id"]
    assert body["sources"][0]["number"] == 1
    assert "ten business days" in fake_model.prompt()
    assert "Question: Can I get my money back?" in fake_model.prompt()


def test_only_chunks_above_the_threshold_reach_the_model(tickets_client, fake_model):
    """Search returns both chunks; the password one is unrelated to refunds and
    scores below the threshold, so the model never sees it."""
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    upload_and_process(tickets_client, "passwords.md", PASSWORDS.encode())

    body = ask(tickets_client, "How long does a refund take?").json()

    assert all(source["score"] >= settings.answer_relevance_threshold for source in body["sources"])
    assert "Forgot password" not in fake_model.prompt()


def test_an_unrelated_question_is_not_found_and_the_model_is_not_called(tickets_client, fake_model):
    """Guard 1. Semantic search alone would still return the refund chunk
    (score < 0.3, v8). The threshold turns that into "not found" - in
    milliseconds, and without giving the model anything to make things up from."""
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    body = ask(tickets_client, "What is the capital of France?").json()

    assert body["answered"] is False
    assert body["reason"] == "no_relevant_sources"
    assert body["sources"] == []
    assert "could not find" in body["answer"]
    assert fake_model.calls == []


def test_when_the_model_does_not_know_the_answer_is_not_found(tickets_client, fake_model):
    """Guard 2: the chunks were relevant enough to send, but did not contain the
    answer. The sources are still returned so a person can look."""
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    fake_model.reply = "I don't know."

    body = ask(tickets_client, "How long does a refund take to appear?").json()

    assert body["answered"] is False
    assert body["reason"] == "model_declined"
    assert "could not find" in body["answer"]
    assert len(body["sources"]) == 1


def test_another_organisations_documents_never_reach_the_model(
    tickets_client, second_org_client, fake_model
):
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    body = ask(second_org_client, "How long does a refund take?").json()

    assert body["answered"] is False
    assert body["sources"] == []
    assert fake_model.calls == []


def test_an_unavailable_model_is_503_and_search_still_works(tickets_client, monkeypatch):
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    def model_missing(messages, max_tokens):
        raise GenerationUnavailable("file not downloaded")

    monkeypatch.setattr("app.answers.service.generate", model_missing)

    response = ask(tickets_client, "How long does a refund take?")
    search = tickets_client.get(
        "/documents/search", params={"q": "How long does a refund take?", "mode": "semantic"}
    )

    assert response.status_code == 503
    assert "mode=semantic" in response.json()["detail"]
    assert search.status_code == 200


def test_questions_are_validated_and_require_a_token(tickets_client, anonymous_client, fake_model):
    assert ask(anonymous_client, "refund").status_code == 401
    assert ask(tickets_client, "").status_code == 422
    assert ask(tickets_client, "x" * 501).status_code == 422
    assert ask(tickets_client, "???").status_code == 422  # no searchable words
    assert fake_model.calls == []


def test_answers_have_their_own_rate_limit(tickets_client, fake_model):
    for _ in range(settings.answer_rate_limit_per_minute):
        assert ask(tickets_client, "What is the capital of France?").status_code == 200

    refused = ask(tickets_client, "What is the capital of France?")

    assert refused.status_code == 429
    # A separate budget: classification still works.
    assert tickets_client.post("/classify", json={"text": "I was charged twice"}).status_code == 200


@pytest.mark.skipif(
    not Path(settings.generation_model_path).exists(),
    reason="generation model not downloaded (python scripts/download_generation_model.py)",
)
def test_the_real_model_answers_from_the_sources(tickets_client):
    """The real model, end to end: the prompt format works with it and the reply
    is used. What it says is judged by scripts/evaluate_rag.py, not here -
    a test cannot tell a good answer from a plausible one."""
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    body = ask(tickets_client, "How long does a refund take?").json()

    assert body["reason"] in ("answered", "model_declined")
    assert body["answer"]
    assert body["sources"][0]["document_title"] == "Refund policy"


# --- Streaming (v11) ------------------------------------------------------------------


class FakeStreamingModel:
    """Yields its reply in fixed pieces, and remembers how many it handed out."""

    def __init__(self, pieces):
        self.pieces = pieces
        self.calls = 0
        self.pieces_sent = 0

    def __call__(self, messages, max_tokens):
        self.calls += 1
        for piece in self.pieces:
            self.pieces_sent += 1
            yield piece


@pytest.fixture
def streaming_model(monkeypatch):
    model = FakeStreamingModel(
        ["Refunds are issued ", "to the original payment method ", "within ten business days."]
    )
    monkeypatch.setattr("app.answers.service.generate_stream", model)
    # The real check loads a 1.1 GB file; the fake model needs nothing loaded.
    monkeypatch.setattr("app.answers.generator.load_model", lambda: None)
    return model


def stream(client, question):
    """POST /answers/stream and return its lines as a list of dicts."""
    response = client.post("/answers/stream", json={"question": question})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines()]


def test_a_stream_sends_sources_first_then_text_then_done(tickets_client, streaming_model):
    refunds = upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    events = stream(tickets_client, "How long does a refund take?")

    assert [e["type"] for e in events[:2]] == ["sources", "text"]
    assert events[0]["sources"][0]["document_id"] == refunds["id"]
    assert events[-1]["type"] == "done"
    assert events[-1]["answered"] is True
    # The pieces add up to the final answer - nothing lost, nothing extra.
    text = "".join(e["text"] for e in events if e["type"] == "text")
    assert text == events[-1]["answer"] == "".join(streaming_model.pieces)


def test_a_stream_for_an_unrelated_question_is_one_done_line(tickets_client, streaming_model):
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    events = stream(tickets_client, "What is the capital of France?")

    assert events == [
        {
            "type": "done",
            "answered": False,
            "reason": "no_relevant_sources",
            "answer": "I could not find an answer to that in the knowledge base.",
        }
    ]
    assert streaming_model.calls == 0


def test_a_refusal_is_never_streamed_as_text_and_generation_stops(tickets_client, streaming_model):
    """The start of the reply is held back until it can be checked. "I don't
    know" must not flash on the user's screen as if it were an answer - and
    the model is not asked for the rest of it."""
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    streaming_model.pieces = ["I don't ", "know. The context ", "is about refunds, ", "not this."] + ["x"] * 50

    events = stream(tickets_client, "How long does a refund take?")

    assert [e["type"] for e in events] == ["sources", "done"]
    assert events[-1]["reason"] == "model_declined"
    assert streaming_model.pieces_sent < 10


def test_a_late_refusal_is_corrected_by_the_done_line(tickets_client, streaming_model):
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    streaming_model.pieces = ["Based on the refund policy above, ", "the context does not mention that."]

    events = stream(tickets_client, "How long does a refund take?")

    assert events[-1]["answered"] is False
    assert events[-1]["reason"] == "model_declined"


def test_a_missing_model_is_a_503_before_the_stream_starts(tickets_client, monkeypatch):
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    def model_missing():
        raise GenerationUnavailable("file not downloaded")

    monkeypatch.setattr("app.answers.generator.load_model", model_missing)

    response = tickets_client.post("/answers/stream", json={"question": "How long does a refund take?"})

    # A normal JSON error with a real status code, not a broken stream.
    assert response.status_code == 503
    assert "mode=semantic" in response.json()["detail"]


def test_a_crash_during_the_stream_ends_with_an_error_line(tickets_client, monkeypatch):
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    monkeypatch.setattr("app.answers.generator.load_model", lambda: None)

    def crashes(messages, max_tokens):
        yield "Refunds are issued to the original payment method, "
        raise RuntimeError("out of memory")

    monkeypatch.setattr("app.answers.service.generate_stream", crashes)

    events = stream(tickets_client, "How long does a refund take?")

    assert events[-1]["type"] == "error"


def test_streaming_shares_the_answer_rate_limit(tickets_client, streaming_model):
    for _ in range(settings.answer_rate_limit_per_minute):
        assert tickets_client.post("/answers/stream", json={"question": "capital of France?"}).status_code == 200

    assert ask(tickets_client, "capital of France?").status_code == 429


@pytest.mark.skipif(
    not Path(settings.generation_model_path).exists(),
    reason="generation model not downloaded (python scripts/download_generation_model.py)",
)
def test_the_real_model_streams_the_same_answer(tickets_client):
    """temperature=0: streamed or not, the model writes the same words."""
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    question = "How long does a refund take?"

    whole = ask(tickets_client, question).json()
    events = stream(tickets_client, question)

    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == whole["answer"]
