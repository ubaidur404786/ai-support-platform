"""API tests for document upload, listing, deletion and search.

tickets_client is authenticated as a user of Acme; second_org_client as a user
of Globex, against the same database. The name tickets_client predates
documents - it means "an authenticated client" (see conftest.py).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.database import SessionLocal
from app.core.errors import StorageError
from app.documents.dependencies import get_document_repository
from app.documents.models import Document, DocumentChunk
from app.documents.repository import PostgresDocumentRepository
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


# --- Upload --------------------------------------------------------------------


def test_upload_markdown_extracts_and_chunks(tickets_client):
    response = upload(tickets_client, "refunds.md", REFUNDS.encode())

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Refund policy"  # from the first heading
    assert body["filename"] == "refunds.md"
    assert body["content_type"] == "markdown"
    assert body["size_bytes"] == len(REFUNDS.encode())
    assert body["page_count"] is None
    assert body["chunk_count"] == 1
    # Metadata only: the text itself is never in this response.
    assert "content" not in body


def test_upload_pdf_counts_pages(tickets_client):
    pdf = make_pdf(["Shipping times", "Orders ship within two days."])

    body = upload(tickets_client, "Shipping.pdf", pdf).json()

    assert body["content_type"] == "pdf"
    assert body["page_count"] == 2
    assert body["title"] == "Shipping"


def test_an_explicit_title_wins(tickets_client):
    body = upload(tickets_client, "r.md", REFUNDS.encode(), title="Refunds (2026)").json()

    assert body["title"] == "Refunds (2026)"


def test_a_long_document_becomes_many_chunks(tickets_client):
    long_text = ("Refunds are issued within ten business days. " * 400).encode()

    body = upload(tickets_client, "long.txt", long_text).json()

    # ~18,000 characters at <= 800 per chunk.
    assert body["chunk_count"] >= 23


def test_the_same_file_twice_is_a_conflict(tickets_client):
    first = upload(tickets_client, "refunds.md", REFUNDS.encode())
    # A different name does not make it a different file: the hash is of the bytes.
    second = upload(tickets_client, "copy-of-refunds.md", REFUNDS.encode())

    assert second.status_code == 409
    assert str(first.json()["id"]) in second.json()["detail"]


def test_two_organisations_may_upload_the_same_file(tickets_client, second_org_client):
    """The uniqueness rule is per organisation: each owns its own copy."""
    assert upload(tickets_client, "refunds.md", REFUNDS.encode()).status_code == 201
    assert upload(second_org_client, "refunds.md", REFUNDS.encode()).status_code == 201


@pytest.mark.parametrize(
    "filename, content, status",
    [
        ("tool.exe", b"MZ binary", 415),
        ("fake.pdf", b"not a pdf at all", 415),
        ("broken.pdf", b"%PDF-1.4 garbage", 422),
        ("scan.pdf", make_pdf([""]), 422),
        ("latin1.txt", "café".encode("latin-1"), 422),
        ("blank.md", b"   \n\n ", 422),
    ],
)
def test_unusable_files_are_refused(tickets_client, filename, content, status):
    response = upload(tickets_client, filename, content)

    assert response.status_code == status
    # Nothing was stored for a refused file.
    assert tickets_client.get("/documents").json()["total"] == 0


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

    body = tickets_client.get("/documents", params={"limit": 2}).json()

    assert body["total"] == 3
    assert [d["filename"] for d in body["items"]] == ["doc2.txt", "doc1.txt"]


def test_get_returns_the_metadata(tickets_client):
    created = upload(tickets_client, "refunds.md", REFUNDS.encode()).json()

    fetched = tickets_client.get(f"/documents/{created['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == created


def test_delete_removes_the_document_and_its_chunks(tickets_client):
    created = upload(tickets_client, "refunds.md", REFUNDS.encode()).json()

    assert tickets_client.delete(f"/documents/{created['id']}").status_code == 204
    assert tickets_client.get(f"/documents/{created['id']}").status_code == 404
    # The chunks went with it (ON DELETE CASCADE): search finds nothing.
    assert tickets_client.get("/documents/search", params={"q": "refund"}).json()["results"] == []
    # Deleting again is 404, not a silent success.
    assert tickets_client.delete(f"/documents/{created['id']}").status_code == 404


def test_another_organisations_document_is_invisible(tickets_client, second_org_client):
    """Read, delete and search all behave as if the document did not exist."""
    created = upload(tickets_client, "refunds.md", REFUNDS.encode()).json()
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
    refunds = upload(tickets_client, "refunds.md", REFUNDS.encode()).json()
    upload(tickets_client, "passwords.md", PASSWORDS.encode())

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
    upload(tickets_client, "refunds.md", REFUNDS.encode())

    results = tickets_client.get("/documents/search", params={"q": "refunded"}).json()["results"]

    assert len(results) == 1


def test_any_ranks_better_matches_first_and_all_requires_every_word(tickets_client):
    refunds = upload(tickets_client, "refunds.md", REFUNDS.encode()).json()
    passwords = upload(tickets_client, "passwords.md", PASSWORDS.encode()).json()
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
    upload(tickets_client, "refunds.md", REFUNDS.encode())

    response = tickets_client.get("/documents/search", params={"q": "refund') | !(x"})

    assert response.status_code == 200
    assert len(response.json()["results"]) == 1


def test_search_results_are_bounded(tickets_client):
    for i in range(3):
        upload(tickets_client, f"r{i}.txt", f"Refund rule number {i}".encode())

    over = tickets_client.get("/documents/search", params={"q": "refund", "limit": 21})
    capped = tickets_client.get("/documents/search", params={"q": "refund", "limit": 2})

    assert over.status_code == 422
    assert len(capped.json()["results"]) == 2


def test_search_requires_a_token(anonymous_client):
    assert anonymous_client.get("/documents/search", params={"q": "refund"}).status_code == 401


# --- Failure paths ------------------------------------------------------------------


def test_storage_failure_is_503(tickets_client):
    class BrokenRepository:
        def add(self, document, chunks):
            raise StorageError("connection refused")

        def get(self, organization_id, document_id):
            raise StorageError("connection refused")

        def get_by_hash(self, organization_id, sha256):
            raise StorageError("connection refused")

        def list(self, organization_id, limit, offset):
            raise StorageError("connection refused")

        def count(self, organization_id):
            raise StorageError("connection refused")

        def delete(self, organization_id, document_id):
            raise StorageError("connection refused")

        def search(self, organization_id, words, limit, match="any"):
            raise StorageError("connection refused")

    tickets_client.app.dependency_overrides[get_document_repository] = BrokenRepository
    try:
        assert upload(tickets_client, "r.md", REFUNDS.encode()).status_code == 503
        assert tickets_client.get("/documents").status_code == 503
        assert tickets_client.get("/documents/1").status_code == 503
        assert tickets_client.delete("/documents/1").status_code == 503
        assert tickets_client.get("/documents/search", params={"q": "x"}).status_code == 503
    finally:
        tickets_client.app.dependency_overrides.clear()


def test_a_failed_chunk_insert_leaves_no_document_behind(tickets_client):
    """Document and chunks are one transaction: all rows or none.

    A chunk with no text violates NOT NULL, so the insert fails after the
    document row was already sent (flushed). The rollback must take the document
    with it - and the error must be a StorageError, not a 409: a NOT NULL
    violation is a bug, not a duplicate.
    """
    upload(tickets_client, "passwords.md", PASSWORDS.encode())
    # Acme and its user are the first rows created after RESTART IDENTITY.
    organization_id, user_id = 1, 1

    session = SessionLocal()
    try:
        repository = PostgresDocumentRepository(session)
        document = Document(
            organization_id=organization_id,
            uploaded_by_user_id=user_id,
            title="Half written",
            filename="half.txt",
            content_type="text",
            size_bytes=4,
            sha256="0" * 64,
            content="text",
            chunk_count=1,
        )
        bad_chunk = DocumentChunk(
            organization_id=organization_id,
            chunk_index=0,
            text=None,
            start_char=0,
            end_char=4,
        )
        with pytest.raises(StorageError):
            repository.add(document, [bad_chunk])

        titles = session.scalars(select(Document.title)).all()
        chunk_rows = session.scalar(select(func.count()).select_from(DocumentChunk))
    finally:
        session.close()

    assert "Half written" not in titles
    assert chunk_rows == 1  # only the chunk of passwords.md


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

    assert statuses == [201, 201, 429]
    assert search.status_code == 200
