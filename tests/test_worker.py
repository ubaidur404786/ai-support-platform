"""Tests for the background worker's job handling (app/documents/processing.py).

The happy path is covered through the API in test_documents_api.py. These are
the situations a queue exists to survive: two workers at once, a crash halfway,
a failure while saving, a document deleted mid-processing.

Documents are uploaded through the real API (tickets_client), then the worker
functions are called directly, one step at a time, so each situation can be
set up exactly.
"""

import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.chunking import Chunk
from app.documents.models import Document, DocumentChunk, DocumentFile
from app.documents.processing import (
    claim_next_document,
    process_document,
    process_next_document,
    requeue_stale_documents,
)


def upload(client, filename, text):
    response = client.post(
        "/documents", files={"file": (filename, text.encode(), "application/octet-stream")}
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def row(document_id) -> Document | None:
    with SessionLocal() as session:
        return session.get(Document, document_id)


def count(model) -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_an_empty_queue_has_nothing_to_do(clean_database):
    with SessionLocal() as session:
        assert process_next_document(session) is False


def test_documents_are_claimed_oldest_first(tickets_client):
    first = upload(tickets_client, "a.txt", "First document")
    upload(tickets_client, "b.txt", "Second document")

    with SessionLocal() as session:
        claimed = claim_next_document(session)

    assert claimed.id == first
    stored = row(first)
    assert stored.status == "processing"
    assert stored.attempts == 1
    assert stored.processing_started_at is not None


def test_two_workers_never_take_the_same_document(tickets_client):
    """SKIP LOCKED: while worker A holds document 1, worker B takes document 2
    instead of waiting for A - or, worse, taking document 1 as well."""
    first = upload(tickets_client, "a.txt", "First document")
    second = upload(tickets_client, "b.txt", "Second document")

    worker_a = SessionLocal()
    worker_b = SessionLocal()
    try:
        # Worker A has locked document 1 and not yet committed.
        locked = worker_a.scalar(
            select(Document).where(Document.id == first).with_for_update()
        )
        assert locked is not None

        claimed_by_b = claim_next_document(worker_b)
    finally:
        worker_a.rollback()
        worker_a.close()
        worker_b.close()

    assert claimed_by_b.id == second


def test_a_failure_while_saving_leaves_no_chunks_and_retries(tickets_client, monkeypatch):
    """Chunks and status are written in one transaction.

    A chunk with no text violates NOT NULL when it is saved. The rollback must
    leave no chunk behind and keep the file, and the document goes back in the
    queue - this kind of failure might not happen next time.
    """
    document_id = upload(tickets_client, "a.txt", "Some document")
    monkeypatch.setattr(
        "app.documents.processing.chunk_text",
        lambda text, max_chars, overlap: [Chunk(index=0, text=None, start=0, end=4)],
    )

    with SessionLocal() as session:
        assert process_next_document(session) is True

    stored = row(document_id)
    assert stored.status == "queued"
    assert stored.attempts == 1
    assert count(DocumentChunk) == 0
    assert count(DocumentFile) == 1  # still there for the next try


def test_repeated_failures_end_in_failed(tickets_client, monkeypatch):
    document_id = upload(tickets_client, "a.txt", "Some document")

    def broken(text, max_chars, overlap):
        raise RuntimeError("a bug in chunking")

    monkeypatch.setattr("app.documents.processing.chunk_text", broken)

    with SessionLocal() as session:
        # One turn per attempt; the queue is then empty.
        turns = 0
        while process_next_document(session):
            turns += 1

    stored = row(document_id)
    assert turns == settings.worker_max_attempts
    assert stored.status == "failed"
    assert stored.attempts == settings.worker_max_attempts
    assert "after 3 attempts" in stored.error_message
    assert count(DocumentFile) == 0


def test_a_document_deleted_during_processing_is_discarded(tickets_client):
    document_id = upload(tickets_client, "a.txt", "Some document")

    with SessionLocal() as session:
        document = claim_next_document(session)
        # The owner deletes it while the worker is busy with it.
        assert tickets_client.delete(f"/documents/{document_id}").status_code == 204
        process_document(session, document)  # must not raise

    assert row(document_id) is None
    assert count(DocumentChunk) == 0


def test_a_worker_that_was_replaced_throws_its_result_away(tickets_client):
    """A worker so slow that its document was declared abandoned and handed to
    another worker must not write a second set of chunks when it finishes."""
    document_id = upload(tickets_client, "a.txt", "Some document")

    with SessionLocal() as session:
        document = claim_next_document(session)
        # Meanwhile, another worker claimed it again after a stale requeue.
        with SessionLocal() as other:
            other.get(Document, document_id).attempts = 2
            other.commit()
        process_document(session, document)

    assert count(DocumentChunk) == 0
    assert row(document_id).status == "processing"  # still the other worker's


def _abandon(document_id, attempts):
    """Make it look as if a worker started this document long ago and died."""
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        document.status = "processing"
        document.attempts = attempts
        document.processing_started_at = datetime.now(timezone.utc) - timedelta(
            seconds=settings.worker_stale_after_seconds + 60
        )
        session.commit()


def test_a_document_abandoned_by_a_crashed_worker_is_requeued(tickets_client):
    document_id = upload(tickets_client, "a.txt", "Some document")
    _abandon(document_id, attempts=1)

    with SessionLocal() as session:
        assert requeue_stale_documents(session) == 1
        # And a working worker then finishes it.
        assert process_next_document(session) is True

    assert row(document_id).status == "ready"


def test_a_document_still_being_processed_is_left_alone(tickets_client):
    document_id = upload(tickets_client, "a.txt", "Some document")
    with SessionLocal() as session:
        claim_next_document(session)  # started just now

        assert requeue_stale_documents(session) == 0

    assert row(document_id).status == "processing"


def test_an_abandoned_document_out_of_attempts_fails(tickets_client):
    document_id = upload(tickets_client, "a.txt", "Some document")
    _abandon(document_id, attempts=settings.worker_max_attempts)

    with SessionLocal() as session:
        requeue_stale_documents(session)

    stored = row(document_id)
    assert stored.status == "failed"
    assert "gave up" in stored.error_message
    assert count(DocumentFile) == 0


def test_the_worker_loop_survives_errors(monkeypatch, caplog):
    """If the database is down, the worker logs, waits and tries again - it
    must not exit. Stopped here by making the wait raise Ctrl+C."""
    from app import worker

    def database_down(session):
        raise ConnectionError("database unavailable")

    def stop(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "requeue_stale_documents", database_down)
    monkeypatch.setattr(worker.time, "sleep", stop)

    with caplog.at_level(logging.INFO, logger="app.worker"):
        worker.main()  # returns instead of raising

    assert "Worker turn failed" in caplog.text
    assert "Worker stopped" in caplog.text


def test_the_worker_process_knows_every_table_it_writes():
    """The worker runs in its own process, which imports far less than the test
    suite does. A foreign key to a table the process never imported fails only
    when the first row is saved. Checked in a fresh interpreter, where nothing
    else has been imported - sorted_tables resolves every foreign key."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.worker; from app.core.database import Base; Base.metadata.sorted_tables",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
