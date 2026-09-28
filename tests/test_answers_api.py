"""Tests for POST /answers (v10): retrieve, check, generate.

Most tests replace the language model with a small fake function. The fake
records what it was sent and returns a fixed reply, so each test can check the
parts that are OUR code: which chunks reach the model, when the model is not
called at all, and how its reply is turned into a response. The real model is
exercised by one test at the end and by scripts/evaluate_rag.py.
"""

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
