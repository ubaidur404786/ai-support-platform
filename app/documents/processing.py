"""The slow half of ingestion, run by the worker process (app/worker.py).

An upload request stores the file and marks the document QUEUED. From there:

    claim_next_document   QUEUED -> PROCESSING   (one worker, one document)
    process_document      read the file -> extract text -> chunk -> embed -> save
                          PROCESSING -> READY    (or FAILED, with a reason)

The queue is simply the documents table: "the next job" is the oldest document
whose status is QUEUED. No separate queue service is needed (ADR-021).
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.documents.chunking import chunk_text
from app.documents.embeddings import embed_texts, to_bytes
from app.documents.extraction import (
    DocumentTooLarge,
    UnreadableDocument,
    UnsupportedDocumentType,
    extract_text,
)
from app.documents.models import (
    FAILED,
    PROCESSING,
    QUEUED,
    READY,
    Document,
    DocumentChunk,
    DocumentFile,
)

logger = logging.getLogger(__name__)

# Problems with the FILE itself. Trying again cannot fix them, so the document
# fails at once and the uploader is told why.
FILE_PROBLEMS = (UnsupportedDocumentType, UnreadableDocument, DocumentTooLarge)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def claim_next_document(session: Session) -> Document | None:
    """Take the oldest QUEUED document and mark it PROCESSING.

    FOR UPDATE locks the row we read; SKIP LOCKED makes a second worker asking
    at the same moment skip that row and take the next one instead of waiting.
    That is what lets two or more workers share one queue without ever
    processing the same document twice.
    """
    document = session.scalar(
        select(Document)
        .where(Document.status == QUEUED)
        .order_by(Document.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if document is None:
        session.commit()  # end the (empty) transaction
        return None

    document.status = PROCESSING
    document.attempts += 1
    document.processing_started_at = _now()
    # Committed straight away: the lock is released, and the PROCESSING status
    # is now what keeps other workers away - even though this one will spend
    # seconds on the file before it writes again.
    session.commit()
    return document


def _lock_if_still_ours(session: Session, document_id: int, attempt: int) -> Document | None:
    """Re-read the document and lock it until our final commit.

    Returns None if our work should be thrown away because, while we were busy:
      - the owner deleted the document, or
      - we were so slow that the document was declared abandoned and handed to
        another worker (its attempts counter moved on).
    """
    document = session.scalar(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()
        # Overwrite the copy this session already holds with the current row.
        .execution_options(populate_existing=True)
    )
    if document is None or document.status != PROCESSING or document.attempts != attempt:
        return None
    return document


def _finish(session: Session, document: Document, status: str, error: str | None = None) -> None:
    document.status = status
    document.error_message = error[:500] if error else None
    document.processed_at = _now()
    # The file has done its job - the text is extracted, or it never will be.
    # A Core DELETE, not session.delete(): it is simply a no-op if the row is gone.
    session.execute(delete(DocumentFile).where(DocumentFile.document_id == document.id))
    session.commit()


# Chunks embedded between two heartbeats. Small enough that a batch takes well
# under WORKER_STALE_AFTER_SECONDS even on a slow CPU (~50 s at 5 chunks/s).
EMBED_BATCH_SIZE = 256


def _heartbeat(session: Session, document_id: int, attempt: int) -> None:
    """Tell the database "this worker is still working on the document".

    requeue_stale_documents treats a document PROCESSING for longer than
    worker_stale_after_seconds as abandoned. Embedding a large document takes
    longer than that (measured in v8: ~7.5 minutes for a 300-page PDF on the
    development laptop), so without this a second worker would take it over
    while the first was still busy - again and again, until it FAILED.
    """
    session.execute(
        update(Document)
        # Only while it is still ours: not if deleted or taken over.
        .where(
            Document.id == document_id,
            Document.status == PROCESSING,
            Document.attempts == attempt,
        )
        .values(processing_started_at=_now())
    )
    session.commit()


def _embed_with_heartbeat(
    session: Session, document_id: int, attempt: int, texts: list[str], app_settings: Settings
) -> list:
    """Embed the texts in batches, sending a heartbeat after each batch."""
    vectors = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        vectors.extend(embed_texts(batch, app_settings.embedding_model))
        _heartbeat(session, document_id, attempt)
    return vectors


def process_document(
    session: Session, document: Document, app_settings: Settings = settings
) -> None:
    """Extract, chunk and save one claimed document."""
    document_id, attempt, filename = document.id, document.attempts, document.filename

    file = session.get(DocumentFile, document_id)
    if file is None:
        logger.info("Document %s was deleted before processing", document_id)
        session.commit()
        return
    data = file.data
    # End the read transaction before the slow part, so the database is not
    # holding anything open for us while we work.
    session.commit()

    # --- The slow part: seconds for a large file, and no database involved. ---
    try:
        extracted = extract_text(filename, data, app_settings.max_document_pages)
        chunks = chunk_text(
            extracted.text,
            app_settings.chunk_max_chars,
            app_settings.chunk_overlap_chars,
        )
    except FILE_PROBLEMS as error:
        current = _lock_if_still_ours(session, document_id, attempt)
        if current is None:
            session.commit()
            return
        _finish(session, current, FAILED, str(error))
        logger.info("Document %s failed: %s", document_id, error)
        return

    # One embedding per chunk (v8). If the model is unavailable this raises, and
    # process_next_document treats it like any unexpected error: back in the
    # queue, up to worker_max_attempts. The file itself is fine, so it is not
    # failed at once like a broken PDF.
    vectors = _embed_with_heartbeat(
        session, document_id, attempt, [chunk.text for chunk in chunks], app_settings
    )

    # --- Save everything in ONE transaction: text, chunks, status. ---
    current = _lock_if_still_ours(session, document_id, attempt)
    if current is None:
        logger.info("Document %s was deleted or taken over; result discarded", document_id)
        session.commit()
        return

    current.content = extracted.text
    current.page_count = extracted.page_count
    current.chunk_count = len(chunks)
    current.embedding_model = app_settings.embedding_model
    session.add_all(
        DocumentChunk(
            document_id=document_id,
            organization_id=current.organization_id,
            chunk_index=chunk.index,
            text=chunk.text,
            start_char=chunk.start,
            end_char=chunk.end,
            embedding=to_bytes(vector),
        )
        # zip pairs each chunk with its vector: same order, same length.
        for chunk, vector in zip(chunks, vectors)
    )
    # _finish commits: the chunks and the READY status become visible together.
    # A search can never see half a document's chunks.
    _finish(session, current, READY)
    logger.info("Document %s ready: %s chunks", document_id, len(chunks))


def process_next_document(session: Session, app_settings: Settings = settings) -> bool:
    """Claim and process one document. Returns False when the queue was empty."""
    document = claim_next_document(session)
    if document is None:
        return False

    document_id, attempt = document.id, document.attempts
    try:
        process_document(session, document, app_settings)
    except Exception:
        # Not a problem with the file - a bug, or the database went away for a
        # moment. Those can succeed on another try.
        logger.exception("Processing document %s failed (attempt %s)", document_id, attempt)
        session.rollback()
        current = _lock_if_still_ours(session, document_id, attempt)
        if current is None:
            session.commit()
        elif attempt >= app_settings.worker_max_attempts:
            _finish(session, current, FAILED, f"Processing failed after {attempt} attempts")
        else:
            current.status = QUEUED  # back in the queue for another try
            session.commit()
    return True


def requeue_stale_documents(session: Session, app_settings: Settings = settings) -> int:
    """Rescue documents whose worker died while processing them.

    A worker that crashes (or is stopped with Ctrl+C) mid-document leaves it
    PROCESSING forever - nothing else would ever pick it up. Anything PROCESSING
    with no heartbeat for longer than worker_stale_after_seconds is treated as
    abandoned: back to QUEUED, or FAILED if it has already used all its attempts.
    """
    cutoff = _now() - timedelta(seconds=app_settings.worker_stale_after_seconds)
    abandoned = (Document.status == PROCESSING) & (Document.processing_started_at < cutoff)

    failed = session.execute(
        update(Document)
        .where(abandoned, Document.attempts >= app_settings.worker_max_attempts)
        .values(
            status=FAILED,
            error_message="Processing did not finish; gave up after repeated attempts",
            processed_at=_now(),
        )
    ).rowcount
    requeued = session.execute(update(Document).where(abandoned).values(status=QUEUED)).rowcount
    if failed:
        # Their files will never be read now.
        failed_ids = select(Document.id).where(Document.status == FAILED)
        session.execute(delete(DocumentFile).where(DocumentFile.document_id.in_(failed_ids)))
    session.commit()

    if failed or requeued:
        logger.warning("Abandoned documents: %s requeued, %s failed", requeued, failed)
    return requeued + failed


def embed_existing_document(session: Session, document: Document, app_settings: Settings = settings) -> int:
    """Give an already-processed document embeddings for all its chunks.

    For documents processed before v8 (embedding_model is NULL), or by an older
    model. Used by scripts/embed_existing_documents.py. Returns the number of
    chunks embedded. Commits once, so a document is either fully embedded with
    the new model or left exactly as it was.
    """
    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        )
    )
    vectors = embed_texts([chunk.text for chunk in chunks], app_settings.embedding_model)
    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = to_bytes(vector)
    document.embedding_model = app_settings.embedding_model
    session.commit()
    return len(chunks)
