"""Business logic for documents: ingest (extract -> chunk -> store) and search."""

# Annotations are read lazily. Without this, "list[...]" inside the class body
# would refer to the method named list defined above it, not the built-in type.
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from app.documents.chunking import chunk_text
from app.documents.extraction import DocumentTooLarge, derive_title, extract_text
from app.documents.models import Document, DocumentChunk
from app.documents.repository import DocumentRepository, Match, SearchHit

logger = logging.getLogger(__name__)

# More words than this adds almost nothing to keyword ranking and makes the query
# more expensive: every word is one more lookup in the GIN index.
MAX_QUERY_WORDS = 32


class DuplicateDocument(Exception):
    def __init__(self, existing_id: int) -> None:
        super().__init__(f"Already uploaded as document {existing_id}")
        self.existing_id = existing_id


class EmptySearchQuery(ValueError):
    pass


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
        max_document_pages: int = 300,
        chunk_max_chars: int = 800,
        chunk_overlap_chars: int = 100,
        max_page_size: int = 200,
        max_search_results: int = 20,
    ) -> None:
        self._repository = repository
        self._max_document_bytes = max_document_bytes
        self._max_document_pages = max_document_pages
        self._chunk_max_chars = chunk_max_chars
        self._chunk_overlap_chars = chunk_overlap_chars
        self._max_page_size = max_page_size
        self._max_search_results = max_search_results

    def ingest(
        self,
        organization_id: int,
        user_id: int,
        filename: str,
        data: bytes,
        title: str | None = None,
    ) -> Document:
        # The router checks size too; the service must not rely on that, for the
        # same reason it clamps page sizes - a worker or CLI calls it directly.
        if len(data) > self._max_document_bytes:
            raise DocumentTooLarge(
                f"File is {len(data)} bytes; the limit is {self._max_document_bytes}"
            )

        # Hash before the expensive work: a file we already have is refused
        # without extracting it again.
        sha256 = hashlib.sha256(data).hexdigest()
        existing = self._repository.get_by_hash(organization_id, sha256)
        if existing is not None:
            raise DuplicateDocument(existing.id)

        extracted = extract_text(filename, data, self._max_document_pages)
        chunks = chunk_text(
            extracted.text, self._chunk_max_chars, self._chunk_overlap_chars
        )

        document = Document(
            # From the authenticated caller, never from the request (ADR-013).
            organization_id=organization_id,
            uploaded_by_user_id=user_id,
            title=(title or "").strip()[:300] or derive_title(filename, extracted.text),
            filename=filename[:255],
            content_type=extracted.content_type,
            size_bytes=len(data),
            page_count=extracted.page_count,
            sha256=sha256,
            content=extracted.text,
            chunk_count=len(chunks),
        )
        rows = [
            DocumentChunk(
                organization_id=organization_id,
                chunk_index=chunk.index,
                text=chunk.text,
                start_char=chunk.start,
                end_char=chunk.end,
            )
            for chunk in chunks
        ]
        stored = self._repository.add(document, rows)
        logger.info(
            "Document ingested: id=%s type=%s bytes=%s pages=%s chunks=%s",
            stored.id,
            stored.content_type,
            stored.size_bytes,
            stored.page_count,
            stored.chunk_count,
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
        self, organization_id: int, query: str, limit: int = 5, match: Match = "any"
    ) -> list[SearchHit]:
        # Keep letters and digits only, lowercased. Everything else - punctuation,
        # operators, quotes - is dropped rather than escaped, because in this
        # version search is about words and nothing else.
        words = re.findall(r"[a-z0-9]+", query.lower())[:MAX_QUERY_WORDS]
        if not words:
            raise EmptySearchQuery("The query contains no searchable words")
        limit = max(1, min(limit, self._max_search_results))
        return self._repository.search(organization_id, words, limit, match)
