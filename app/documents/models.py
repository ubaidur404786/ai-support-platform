"""Knowledge-base documents and the chunks they are split into.

A document is what a person uploads: "Refund policy.pdf". A chunk is what a
search returns: the one paragraph of that policy that answers the question.
Retrieval, embeddings and generated answers all work on chunks, so the chunk is
the unit this schema is designed around - not the file.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)

# TSVECTOR is PostgreSQL's type for "text prepared for full-text search": the
# words reduced to their stems ("refunds" -> "refund") with positions, and
# common words ("the", "is") removed.
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

# pgvector adds a "vector" column type to PostgreSQL (and this package teaches
# SQLAlchemy about it): a fixed-size list of numbers the database itself can
# compare, sort by distance, and index.
from pgvector.sqlalchemy import Vector

from app.core.database import Base
from app.documents.embeddings import EMBEDDING_DIMENSIONS


def _now() -> datetime:
    return datetime.now(timezone.utc)


# The life of an upload since v7. The request only stores the file (QUEUED); the
# worker picks it up (PROCESSING) and either finishes it (READY) or gives up on
# it (FAILED, with the reason in error_message). Only READY documents have chunks,
# so only READY documents can be found by search.
QUEUED = "queued"
PROCESSING = "processing"
READY = "ready"
FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Owned by an organisation, exactly like a ticket (ADR-013). A document that
    # leaks across tenants is worse than a ticket that does: its text will later
    # be pasted into generated answers.
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    # Who uploaded it. Not used for access - the organisation owns the document -
    # but the first "who did this?" record in the system.
    uploaded_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    title: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(255))
    # Our own detected type ("pdf", "markdown", "text"), not the client's
    # Content-Type header, which the client can set to anything.
    content_type: Mapped[str] = mapped_column(String(20))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # PDFs only; text files have no pages.
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # SHA-256 of the uploaded bytes: identical files have identical hashes. Used
    # to refuse a second upload of the same file (see the unique constraint).
    sha256: Mapped[str] = mapped_column(String(64))

    # The full extracted text, written by the worker; empty (NULL) until then.
    # The original file is not kept once processed - only what we extracted from
    # it (ADR-019).
    #
    # deferred=True: not loaded with the rest of the row, only if the code actually
    # reads document.content. Measured in v7: listing 5 documents took ~200 ms
    # because every list query read up to 5 MB of text per row that the response
    # never contained.
    content: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    # --- Processing state (v7) ---------------------------------------------------
    # Indexed because the worker asks "which documents are queued?" every second.
    status: Mapped[str] = mapped_column(String(20), default=QUEUED, index=True)
    # Why processing failed, in words the uploader can act on ("Encrypted PDFs
    # are not supported"). NULL unless status is FAILED.
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # How many times a worker has started on this document. Limits retries.
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # When the worker last showed it was alive: set when it starts on the
    # document, and since v8 refreshed after every batch of embeddings (a
    # heartbeat). A document abandoned by a crashed worker is recognised by it:
    # still PROCESSING, no sign of life for a long time.
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When it became READY or FAILED.
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Embeddings (v8) -----------------------------------------------------------
    # Which model produced this document's chunk embeddings, e.g.
    # "sentence-transformers/all-MiniLM-L6-v2". Embeddings from two different
    # models live in different spaces and must never be compared, so semantic
    # search only uses documents embedded by the model it is using now. NULL =
    # not embedded yet (documents from before v8, until the backfill script runs).
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        # Per organisation, not global: two companies may upload the same public
        # PDF, and each must own its copy. Enforced by the database, so two
        # simultaneous uploads of one file cannot both succeed.
        UniqueConstraint(
            "organization_id", "sha256", name="uq_documents_organization_id_sha256"
        ),
    )


class DocumentFile(Base):
    """The uploaded bytes, waiting for the worker.

    The request and the worker are different processes, so the file needs a
    place both can reach. PostgreSQL is that place: it is already there, the file
    is written in the same transaction as its document row (no document without
    its file, no file without its document), and uploads are capped at 5 MB.

    A separate table, not a column on documents, so that listing documents never
    reads file bytes. The row is deleted as soon as the worker has finished.
    """

    __tablename__ = "document_files"

    # The document's id is also this table's primary key: exactly one file per
    # document. CASCADE: deleting a queued document deletes its file too.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    # LargeBinary is PostgreSQL's BYTEA: raw bytes, not text.
    data: Mapped[bytes] = mapped_column(LargeBinary)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)

    # ondelete="CASCADE" makes PostgreSQL delete a document's chunks when the
    # document is deleted. Done by the database, in the same statement, so a
    # crash halfway cannot leave chunks pointing at nothing.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )

    # Copied from the document on purpose (denormalised). Search runs over chunks,
    # and filtering them by organisation directly means the tenant condition does
    # not depend on remembering a JOIN. The same "cannot be forgotten" reasoning
    # as the required organization_id argument in v4.
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )

    # Position within the document, from 0. With document_id, identifies the
    # passage for a citation: "Refund policy, part 3".
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # Where the chunk sits in documents.content, so a result can be shown in
    # context and neighbouring chunks found.
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)

    # A generated column: PostgreSQL computes it from `text` on every insert and
    # update, so it can never drift out of step with the text. The application
    # never writes it. 'english' selects the stemming rules and stop words.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )

    # The chunk's meaning as 384 numbers (see embeddings.py). Written by the
    # worker together with the chunk. NULL only for chunks created before v8 and
    # not yet backfilled.
    #
    # v8 stored these as raw bytes, which only Python could understand, so every
    # search had to copy every vector out of the database (ADR-024). Since v9 it
    # is a pgvector column: PostgreSQL computes the distances itself and can use
    # the index below (ADR-025).
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )

    __table_args__ = (
        # GIN ("generalised inverted index") maps each word to the rows that
        # contain it - the index at the back of a book. Without it, every search
        # would read every chunk.
        Index(
            "ix_document_chunks_search_vector", "search_vector", postgresql_using="gin"
        ),
        # HNSW ("hierarchical navigable small world"): a graph linking each
        # vector to its near neighbours. A search starts at an entry point and
        # keeps stepping to the neighbour closest to the question, so it visits
        # a few hundred vectors instead of all of them. The price: it is
        # APPROXIMATE - it can miss a true nearest neighbour (measured in v9).
        #
        # vector_cosine_ops: the index is built for cosine distance, the <=>
        # operator. A query sorting by another operator cannot use it.
        # m / ef_construction: pgvector's defaults, written out so they are
        # visible (links per vector / effort while building).
        Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
