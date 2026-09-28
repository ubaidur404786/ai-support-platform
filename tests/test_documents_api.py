"""API tests for document upload, listing, deletion and search.

tickets_client is authenticated as a user of Acme; second_org_client as a user
of Globex, against the same database. The name tickets_client predates
documents - it means "an authenticated client" (see conftest.py).

Since v7 an upload only queues the document. process_queue() does, inside the
test, what the worker process does in real life. The worker's own edge cases
(crashes, retries, two workers) are in test_worker.py.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.core.config import Settings
from app.core.database import SessionLocal
from app.core.errors import StorageError
from app.documents.dependencies import get_document_repository
from app.documents.embeddings import EmbeddingUnavailable
from app.documents.models import Document, DocumentFile
from app.documents.processing import process_next_document
from app.main import create_app
from tests.conftest import _register_and_login
from tests.pdf_factory import make_pdf

REFUNDS = (
    "# Refund policy\n\n"
    "Refunds are issued to the original payment method within ten business days.\n\n"
    "Annual plans can be refunded within 30 days of purchase."
)
PASSWORDS = (
    "# Resetting your password\n\n"
    "Use the Forgot password link on the sign-in page. The reset email expires "
    "after one hour."
)


def upload(client, filename, content, title=None):
    data = {"title": title} if title is not None else None
    return client.post(
        "/documents", files={"file": (filename, content, "application/octet-stream")}, data=data
    )


def process_queue():
    """Do what the worker process does: process every queued document."""
    with SessionLocal() as session:
        while process_next_document(session):
            pass


def upload_and_process(client, filename, content, title=None):
    """Upload, let the worker run, and return the document as it is now."""
    response = upload(client, filename, content, title)
    assert response.status_code == 202, response.text
    process_queue()
    return client.get(f"/documents/{response.json()['id']}").json()


def waiting_files() -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(DocumentFile))


# --- Upload ---------------------------------------------------------------------


def test_upload_is_accepted_and_queued(tickets_client):
    response = upload(tickets_client, "refunds.md", REFUNDS.encode())

    # 202: stored, not yet processed. Nothing slow happened in this request.
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["title"] == "Refund policy"  # from the first heading
    assert body["filename"] == "refunds.md"
    assert body["content_type"] == "markdown"
    assert body["size_bytes"] == len(REFUNDS.encode())
    assert body["chunk_count"] == 0
    assert body["processed_at"] is None
    # Metadata only: the text itself is never in this response.
    assert "content" not in body
    assert waiting_files() == 1


def test_the_worker_makes_it_ready(tickets_client):
    body = upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    assert body["status"] == "ready"
    assert body["error_message"] is None
    assert body["chunk_count"] == 1
    assert body["page_count"] is None
    assert body["processed_at"] is not None
    # The file is deleted once its text is extracted.
    assert waiting_files() == 0


def test_a_queued_document_is_not_searchable_yet(tickets_client):
    upload(tickets_client, "refunds.md", REFUNDS.encode())

    before = tickets_client.get("/documents/search", params={"q": "refund"}).json()["results"]
    process_queue()
    after = tickets_client.get("/documents/search", params={"q": "refund"}).json()["results"]

    assert before == []
    assert len(after) == 1


def test_upload_pdf_counts_pages(tickets_client):
    pdf = make_pdf(["Shipping times", "Orders ship within two days."])

    body = upload_and_process(tickets_client, "Shipping.pdf", pdf)

    assert body["content_type"] == "pdf"
    assert body["page_count"] == 2
    assert body["title"] == "Shipping"


def test_an_explicit_title_wins(tickets_client):
    body = upload(tickets_client, "r.md", REFUNDS.encode(), title="Refunds (2026)").json()

    assert body["title"] == "Refunds (2026)"


def test_a_long_document_becomes_many_chunks(tickets_client):
    long_text = ("Refunds are issued within ten business days. " * 400).encode()

    body = upload_and_process(tickets_client, "long.txt", long_text)

    # ~18,000 characters at <= 800 per chunk.
    assert body["chunk_count"] >= 23


def test_the_same_file_twice_is_a_conflict(tickets_client):
    first = upload(tickets_client, "refunds.md", REFUNDS.encode())
    # A different name does not make it a different file: the hash is of the bytes.
    # Refused while the first copy is still queued, too.
    second = upload(tickets_client, "copy-of-refunds.md", REFUNDS.encode())

    assert second.status_code == 409
    assert str(first.json()["id"]) in second.json()["detail"]


def test_two_organisations_may_upload_the_same_file(tickets_client, second_org_client):
    """The uniqueness rule is per organisation: each owns its own copy."""
    assert upload(tickets_client, "refunds.md", REFUNDS.encode()).status_code == 202
    assert upload(second_org_client, "refunds.md", REFUNDS.encode()).status_code == 202


@pytest.mark.parametrize(
    "filename, content",
    [
        ("tool.exe", b"MZ binary"),
        ("fake.pdf", b"not a pdf at all"),
    ],
)
def test_unsupported_types_are_refused_at_upload(tickets_client, filename, content):
    """Name and first bytes are enough to know - no reason to queue it."""
    response = upload(tickets_client, filename, content)

    assert response.status_code == 415
    assert tickets_client.get("/documents").json()["total"] == 0
    assert waiting_files() == 0


@pytest.mark.parametrize(
    "filename, content, reason",
    [
        ("broken.pdf", b"%PDF-1.4 garbage", "Could not read PDF"),
        ("scan.pdf", make_pdf([""]), "No extractable text"),
        ("latin1.txt", "caf\u00e9".encode("latin-1"), "UTF-8"),
        ("blank.md", b"   \n\n ", "No extractable text"),
    ],
)
def test_unreadable_files_fail_with_a_reason(tickets_client, filename, content, reason):
    """Only reading the file shows these problems, so the worker finds them.
    The document stays visible as "failed", with a reason the uploader can act on."""
    assert upload(tickets_client, filename, content).status_code == 202

    process_queue()

    [document] = tickets_client.get("/documents").json()["items"]
    assert document["status"] == "failed"
    assert reason in document["error_message"]
    assert document["chunk_count"] == 0
    assert waiting_files() == 0


def test_a_pdf_over_the_page_limit_fails(tickets_client, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "max_document_pages", 2)

    body = upload_and_process(tickets_client, "long.pdf", make_pdf(["a", "b", "c"]))

    assert body["status"] == "failed"
    assert "3 pages" in body["error_message"]


def test_a_full_queue_is_429(tickets_client, monkeypatch):
    """Backpressure: an organisation cannot queue work faster than it is done."""
    from app.core import config

    monkeypatch.setattr(config.settings, "max_pending_documents_per_organization", 2)

    statuses = [
        upload(tickets_client, f"doc{i}.txt", f"Document {i}".encode()).status_code
        for i in range(3)
    ]
    process_queue()
    after_processing = upload(tickets_client, "doc3.txt", b"Document 3")

    assert statuses == [202, 202, 429]
    assert after_processing.status_code == 202


def test_a_file_over_the_size_limit_is_413(tickets_client, monkeypatch):
    from app.core import config

    # Shrink the limit rather than build a 5 MB file for every test run.
    monkeypatch.setattr(config.settings, "max_document_bytes", 100)

    response = upload(tickets_client, "big.txt", b"a" * 101)

    assert response.status_code == 413


def test_upload_requires_a_token(anonymous_client):
    assert upload(anonymous_client, "refunds.md", REFUNDS.encode()).status_code == 401


def test_an_oversized_body_is_refused_before_authentication(anonymous_client):
    """Measured in v6: without the middleware, FastAPI read a whole 200 MB body
    before its authentication dependency could answer 401. Now the declared size
    alone is enough - 413 for an anonymous caller, with no body read."""
    response = anonymous_client.post(
        "/documents",
        content=b"x" * 10,
        headers={"Content-Length": str(10**9), "Content-Type": "multipart/form-data; boundary=b"},
    )

    assert response.status_code == 413


# --- Read and delete -------------------------------------------------------------


def test_list_is_newest_first_and_paged(tickets_client):
    for i in range(3):
        upload(tickets_client, f"doc{i}.txt", f"Document number {i}".encode())
    process_queue()

    body = tickets_client.get("/documents", params={"limit": 2}).json()

    assert body["total"] == 3
    assert [d["filename"] for d in body["items"]] == ["doc2.txt", "doc1.txt"]


def test_get_follows_the_status(tickets_client):
    created = upload(tickets_client, "refunds.md", REFUNDS.encode()).json()

    queued = tickets_client.get(f"/documents/{created['id']}")
    process_queue()
    ready = tickets_client.get(f"/documents/{created['id']}")

    assert queued.status_code == 200
    assert queued.json() == created
    assert ready.json()["status"] == "ready"


def test_delete_removes_the_document_and_its_chunks(tickets_client):
    created = upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    assert tickets_client.delete(f"/documents/{created['id']}").status_code == 204
    assert tickets_client.get(f"/documents/{created['id']}").status_code == 404
    # The chunks went with it (ON DELETE CASCADE): search finds nothing.
    assert tickets_client.get("/documents/search", params={"q": "refund"}).json()["results"] == []
    # Deleting again is 404, not a silent success.
    assert tickets_client.delete(f"/documents/{created['id']}").status_code == 404


def test_deleting_a_queued_document_removes_its_file(tickets_client):
    created = upload(tickets_client, "refunds.md", REFUNDS.encode()).json()

    assert tickets_client.delete(f"/documents/{created['id']}").status_code == 204
    assert waiting_files() == 0
    # Nothing left for the worker to do.
    with SessionLocal() as session:
        assert process_next_document(session) is False


def test_another_organisations_document_is_invisible(tickets_client, second_org_client):
    """Read, delete and search all behave as if the document did not exist."""
    created = upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    document_id = created["id"]

    assert second_org_client.get(f"/documents/{document_id}").status_code == 404
    assert second_org_client.delete(f"/documents/{document_id}").status_code == 404
    assert second_org_client.get("/documents").json()["total"] == 0
    assert (
        second_org_client.get("/documents/search", params={"q": "refund"}).json()["results"]
        == []
    )
    # And it is still there for its owner - proving the filter is scoped, not broken.
    assert tickets_client.get(f"/documents/{document_id}").status_code == 200


# --- Search -------------------------------------------------------------------------


def test_search_returns_the_matching_passage(tickets_client):
    refunds = upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    upload_and_process(tickets_client, "passwords.md", PASSWORDS.encode())

    body = tickets_client.get("/documents/search", params={"q": "refund"}).json()

    assert body["query"] == "refund"
    assert len(body["results"]) == 1
    hit = body["results"][0]
    assert hit["document_id"] == refunds["id"]
    assert hit["document_title"] == "Refund policy"
    assert hit["chunk_index"] == 0
    assert "original payment method" in hit["text"]
    assert hit["rank"] > 0


def test_search_matches_word_forms(tickets_client):
    """Stemming: "refunded" and "refunds" both reduce to "refund"."""
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    results = tickets_client.get("/documents/search", params={"q": "refunded"}).json()["results"]

    assert len(results) == 1


def test_any_ranks_better_matches_first_and_all_requires_every_word(tickets_client):
    refunds = upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    passwords = upload_and_process(tickets_client, "passwords.md", PASSWORDS.encode())
    query = {"q": "reset password email"}

    any_hits = tickets_client.get("/documents/search", params=query).json()["results"]
    all_hits = tickets_client.get(
        "/documents/search", params={**query, "match": "all"}
    ).json()["results"]

    assert any_hits[0]["document_id"] == passwords["id"]
    assert [h["document_id"] for h in all_hits] == [passwords["id"]]
    # A synonym the text does not contain finds nothing - keyword search knows
    # words, not meaning. The measured version of this is the v6 evaluation.
    assert tickets_client.get(
        "/documents/search", params={"q": "money back", "match": "all"}
    ).json()["results"] == []
    assert refunds["id"] not in [h["document_id"] for h in all_hits]


@pytest.mark.parametrize("query", ["???", "&|!()", "   "])
def test_a_query_with_no_words_is_422(tickets_client, query):
    """Operators are stripped, not passed to to_tsquery, where they would be
    query syntax - or a database error."""
    response = tickets_client.get("/documents/search", params={"q": query})

    assert response.status_code == 422


def test_operator_characters_inside_a_query_are_harmless(tickets_client):
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    response = tickets_client.get("/documents/search", params={"q": "refund') | !(x"})

    assert response.status_code == 200
    assert len(response.json()["results"]) == 1


def test_search_results_are_bounded(tickets_client):
    for i in range(3):
        upload_and_process(tickets_client, f"r{i}.txt", f"Refund rule number {i}".encode())

    over = tickets_client.get("/documents/search", params={"q": "refund", "limit": 21})
    capped = tickets_client.get("/documents/search", params={"q": "refund", "limit": 2})

    assert over.status_code == 422
    assert len(capped.json()["results"]) == 2


def test_search_requires_a_token(anonymous_client):
    assert anonymous_client.get("/documents/search", params={"q": "refund"}).status_code == 401


# --- Semantic search (v8) -------------------------------------------------------------


def semantic(client, query, **params):
    return client.get("/documents/search", params={"q": query, "mode": "semantic", **params})


def test_semantic_search_finds_a_paraphrase_that_keyword_search_misses(tickets_client):
    refunds = upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    upload_and_process(tickets_client, "passwords.md", PASSWORDS.encode())
    question = "Can I get my money back?"  # no word in common with the refund policy

    keyword = tickets_client.get("/documents/search", params={"q": question}).json()
    meaning = semantic(tickets_client, question).json()

    assert keyword["results"] == []
    assert meaning["mode"] == "semantic"
    assert meaning["results"][0]["document_id"] == refunds["id"]
    # Cosine similarity: the refund chunk is clearly closer than the other one.
    first, second = meaning["results"][0]["rank"], meaning["results"][1]["rank"]
    assert first > second


def test_semantic_search_always_returns_the_closest_chunks(tickets_client):
    """Unlike keyword search it never comes back empty - even for a question the
    knowledge base cannot answer. The score is low, but a result is returned.
    Something later (a threshold, a generated answer) has to judge it."""
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    results = semantic(tickets_client, "What is the capital of France?").json()["results"]

    assert len(results) == 1
    assert results[0]["rank"] < 0.3


def test_semantic_search_stays_inside_the_organisation(tickets_client, second_org_client):
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    assert semantic(second_org_client, "money back").json()["results"] == []


def test_documents_embedded_by_another_model_are_not_compared(tickets_client):
    """Vectors from two models live in different spaces: comparing them gives a
    number, but a meaningless one. Such documents are left out until re-embedded."""
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())
    with SessionLocal() as session:
        session.execute(update(Document).values(embedding_model="some-older-model"))
        session.commit()

    assert semantic(tickets_client, "money back").json()["results"] == []
    # Keyword search does not use embeddings and still finds it.
    keyword = tickets_client.get("/documents/search", params={"q": "refund"}).json()
    assert len(keyword["results"]) == 1


def test_semantic_search_is_bounded_and_rejects_empty_queries(tickets_client):
    for i in range(3):
        upload_and_process(tickets_client, f"r{i}.txt", f"Refund rule number {i}".encode())

    assert len(semantic(tickets_client, "refund", limit=2).json()["results"]) == 2
    assert semantic(tickets_client, "refund", limit=21).status_code == 422
    assert semantic(tickets_client, "???").status_code == 422


def test_an_unavailable_model_is_503_and_keyword_search_still_works(tickets_client, monkeypatch):
    upload_and_process(tickets_client, "refunds.md", REFUNDS.encode())

    def model_missing(texts, model_name):
        raise EmbeddingUnavailable("model not downloaded")

    monkeypatch.setattr("app.documents.service.embed_texts", model_missing)

    response = semantic(tickets_client, "money back")
    keyword = tickets_client.get("/documents/search", params={"q": "refund"})

    assert response.status_code == 503
    assert "mode=keyword" in response.json()["detail"]
    assert keyword.status_code == 200
    assert len(keyword.json()["results"]) == 1


# --- Failure paths ------------------------------------------------------------------


def test_storage_failure_is_503(tickets_client):
    class BrokenRepository:
        def add(self, document, data):
            raise StorageError("connection refused")

        def get(self, organization_id, document_id):
            raise StorageError("connection refused")

        def get_by_hash(self, organization_id, sha256):
            raise StorageError("connection refused")

        def list(self, organization_id, limit, offset):
            raise StorageError("connection refused")

        def count(self, organization_id):
            raise StorageError("connection refused")

        def count_pending(self, organization_id):
            raise StorageError("connection refused")

        def delete(self, organization_id, document_id):
            raise StorageError("connection refused")

        def search(self, organization_id, words, limit, match="any"):
            raise StorageError("connection refused")

        def semantic_search(self, organization_id, query_vector, model_name, limit):
            raise StorageError("connection refused")

    tickets_client.app.dependency_overrides[get_document_repository] = BrokenRepository
    try:
        assert upload(tickets_client, "r.md", REFUNDS.encode()).status_code == 503
        assert tickets_client.get("/documents").status_code == 503
        assert tickets_client.get("/documents/1").status_code == 503
        assert tickets_client.delete("/documents/1").status_code == 503
        assert tickets_client.get("/documents/search", params={"q": "x"}).status_code == 503
        semantic_query = {"q": "x", "mode": "semantic"}
        assert tickets_client.get("/documents/search", params=semantic_query).status_code == 503
    finally:
        tickets_client.app.dependency_overrides.clear()


def test_uploads_are_rate_limited_per_user(clean_database):
    with TestClient(create_app(Settings(ingestion_rate_limit_per_minute=2))) as client:
        token = _register_and_login(client, "Acme", "user@acme.example")
        client.headers.update({"Authorization": f"Bearer {token}"})

        statuses = [
            upload(client, f"doc{i}.txt", f"Document {i}".encode()).status_code
            for i in range(3)
        ]
        # Reads and searches spend no ingestion budget.
        search = client.get("/documents/search", params={"q": "document"})

    assert statuses == [202, 202, 429]
    assert search.status_code == 200
