"""Request and response shapes for the documents endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    """A document's metadata.

    Deliberately without `content`: a document can be megabytes of text, and
    v3's rule is that a response must not grow with the size of stored data.
    The text is reached through search, one chunk at a time.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    filename: str
    content_type: str
    size_bytes: int
    # queued -> processing -> ready | failed. Only "ready" documents are searchable.
    status: str
    # Why it failed, when status is "failed".
    error_message: str | None
    # Known once processed; null / 0 before that.
    page_count: int | None
    chunk_count: int
    created_at: datetime
    processed_at: datetime | None


class DocumentListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DocumentResponse]


class SearchResult(BaseModel):
    document_id: int
    document_title: str
    # Which passage of the document: the citation a later RAG answer will give.
    chunk_index: int
    text: str
    # Keyword-overlap score. Comparable within one query's results only.
    rank: float


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
