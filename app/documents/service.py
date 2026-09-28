"""Business logic for documents: accept an upload, read, delete and search.

Since v7 the slow part - extracting and chunking - is not here. It runs in the
worker (app/documents/processing.py). An upload only checks what is cheap to
check, stores the file, and returns.
"""

# Annotations are read lazily. Without this, "list[...]" inside the class body
# would refer to the method named list defined above it, not the built-in type.
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.documents.embeddings import embed_texts
from app.documents.extraction import DocumentTooLarge, derive_title, detect_type
from app.documents.models import QUEUED, Document
from app.documents.repository import DocumentRepository, Match, SearchHit

logger = logging.getLogger(__name__)

# More words than this adds almost nothing to keyword ranking and makes the query
# more expensive: every word is one more lookup in the GIN index.
MAX_QUERY_WORDS = 32

# "keyword": chunks that share words with the query (PostgreSQL full-text, v6).
# "semantic": chunks whose embedding is closest to the query's (v8).
SearchMode = Literal["keyword", "semantic"]


class DuplicateDocument(Exception):
    def __init__(self, existing_id: int) -> None:
        super().__init__(f"Already uploaded as document {existing_id}")
        self.existing_id = existing_id


class EmptySearchQuery(ValueError):
    pass


class TooManyPendingDocuments(Exception):
    """The organisation already has a full queue. Becomes 429."""


@dataclass(frozen=True)
class DocumentPage:
    items: list[Document]
    total: int
    limit: int
    offset: int


class DocumentService:
    def __init__(
        self,
        repository: DocumentRepository,
        max_document_bytes: int = 5_000_000,
        max_pending_documents: int = 50,
        max_page_size: int = 200,
        max_search_results: int = 20,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    ) -> None:
        self._repository = repository
        self._max_document_bytes = max_document_bytes
        self._max_pending_documents = max_pending_documents
        self._max_page_size = max_page_size
        self._max_search_results = max_search_results
        self._embedding_model = embedding_model

    def upload(
        self,
        organization_id: int,
        user_id: int,
        filename: str,
        data: bytes,
        title: str | None = None,
    ) -> Document:
        """Check the upload, store it as QUEUED, and return straight away.

        Only checks that take microseconds happen here: size, file type, "have
        we seen this file?", "is the queue full?". Anything that needs the file
        to be read - broken PDFs, too many pages, no text - is found by the
        worker and reported through the document's status.
        """
        # The router checks size too; the service must not rely on that, because
        # it can be called without HTTP (a script, a test).
        if len(data) > self._max_document_bytes:
            raise DocumentTooLarge(
                f"File is {len(data)} bytes; the limit is {self._max_document_bytes}"
            )
        # Reads only the extension and the first bytes, so still answered now.
        content_type = detect_type(filename, data)

        sha256 = hashlib.sha256(data).hexdigest()
        existing = self._repository.get_by_hash(organization_id, sha256)
        if existing is not None:
            raise DuplicateDocument(existing.id)

        # Backpressure: stop accepting work faster than it can be done. Without
        # this the queue - and the wait for everyone behind it - grows forever.
        if self._repository.count_pending(organization_id) >= self._max_pending_documents:
            raise TooManyPendingDocuments(
                f"{self._max_pending_documents} documents are already waiting to be "
                "processed; try again when some are ready"
            )

        if not title or not title.strip():
            # A Markdown heading is on the first line, so reading the first
            # kilobyte is enough - no need to decode the whole file here.
            first_text = data[:1000].decode("utf-8-sig", errors="ignore").lstrip()
            title = derive_title(filename, first_text if content_type != "pdf" else "")

        document = Document(
            # From the authenticated caller, never from the request (ADR-013).
            organization_id=organization_id,
            uploaded_by_user_id=user_id,
            title=title.strip()[:300],
            filename=filename[:255],
            content_type=content_type,
            size_bytes=len(data),
            sha256=sha256,
            status=QUEUED,
            attempts=0,
            # The worker fills in content, page_count and chunk_count later.
            chunk_count=0,
        )
        stored = self._repository.add(document, data)
        logger.info(
            "Document queued: id=%s type=%s bytes=%s",
            stored.id,
            stored.content_type,
            stored.size_bytes,
        )
        return stored

    def get(self, organization_id: int, document_id: int) -> Document | None:
        return self._repository.get(organization_id, document_id)

    def list(self, organization_id: int, limit: int = 50, offset: int = 0) -> DocumentPage:
        limit = max(1, min(limit, self._max_page_size))
        offset = max(0, offset)
        return DocumentPage(
            items=self._repository.list(organization_id, limit, offset),
            total=self._repository.count(organization_id),
            limit=limit,
            offset=offset,
        )

    def delete(self, organization_id: int, document_id: int) -> bool:
        return self._repository.delete(organization_id, document_id)

    def search(
        self,
        organization_id: int,
        query: str,
        limit: int = 5,
        match: Match = "any",
        mode: SearchMode = "keyword",
    ) -> list[SearchHit]:
        # Keep letters and digits only, lowercased. Everything else - punctuation,
        # operators, quotes - is dropped rather than escaped, because keyword
        # search is about words and nothing else.
        words = re.findall(r"[a-z0-9]+", query.lower())[:MAX_QUERY_WORDS]
        # The same rule for both modes: "?!" means nothing to either of them.
        if not words:
            raise EmptySearchQuery("The query contains no searchable words")
        limit = max(1, min(limit, self._max_search_results))

        if mode == "semantic":
            # The model reads the question as written - punctuation, word order
            # and all - so it gets the original text, not the cleaned words.
            # Raises EmbeddingUnavailable if the model cannot run.
            query_vector = embed_texts([query.strip()], self._embedding_model)[0]
            return self._repository.semantic_search(
                organization_id, query_vector, self._embedding_model, limit
            )
        return self._repository.search(organization_id, words, limit, match)
