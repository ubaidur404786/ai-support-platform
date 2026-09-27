"""HTTP endpoints for knowledge-base documents."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.auth.dependencies import get_current_user, limit_ingestion
from app.auth.models import User
from app.core.config import settings
from app.core.errors import AlreadyExistsError, StorageError
from app.documents.dependencies import get_document_service
from app.documents.extraction import (
    DocumentTooLarge,
    UnreadableDocument,
    UnsupportedDocumentType,
)
from app.documents.schemas import (
    DocumentListResponse,
    DocumentResponse,
    SearchResponse,
    SearchResult,
)
from app.documents.service import DocumentService, DuplicateDocument, EmptySearchQuery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


def _storage_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Storage is unavailable")


# Plain "def": extraction and chunking are CPU work and the database driver is
# synchronous, so FastAPI runs this in its thread pool.
@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    # File(...) reads one part of a multipart/form-data body - the format
    # browsers and HTTP clients use to send files. Needs python-multipart.
    file: UploadFile = File(...),
    # Optional; otherwise the first Markdown heading or the file name is used.
    title: str | None = Form(default=None, max_length=300),
    current_user: User = Depends(get_current_user),
    _: None = Depends(limit_ingestion),
    service: DocumentService = Depends(get_document_service),
) -> DocumentResponse:
    # Read one byte past the limit: enough to know the file is too big without
    # holding an arbitrarily large file in memory.
    data = file.file.read(settings.max_document_bytes + 1)
    if len(data) > settings.max_document_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds {settings.max_document_bytes} bytes",
        )

    try:
        document = service.ingest(
            organization_id=current_user.organization_id,
            user_id=current_user.id,
            filename=file.filename or "upload",
            data=data,
            title=title,
        )
    except UnsupportedDocumentType as error:
        # 415 Unsupported Media Type: we do not read this kind of file at all.
        raise HTTPException(status_code=415, detail=str(error))
    except DocumentTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error))
    except UnreadableDocument as error:
        # 422: a supported type, but this particular file cannot be used.
        raise HTTPException(status_code=422, detail=str(error))
    except DuplicateDocument as error:
        raise HTTPException(status_code=409, detail=str(error))
    except AlreadyExistsError:
        # The same duplicate, detected by the database's unique constraint
        # because a concurrent upload won the race after our own check.
        raise HTTPException(status_code=409, detail="This document has already been uploaded")
    except StorageError:
        logger.exception("Storage unavailable while ingesting a document")
        raise _storage_unavailable()

    return DocumentResponse.model_validate(document)


@router.get("", response_model=DocumentListResponse)
def list_documents(
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentListResponse:
    try:
        page = service.list(current_user.organization_id, limit=limit, offset=offset)
    except StorageError:
        raise _storage_unavailable()
    return DocumentListResponse(
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        items=[DocumentResponse.model_validate(d) for d in page.items],
    )


# Declared BEFORE /{document_id}. Routes match in order, and "search" would
# otherwise be tried as a document id - and rejected with 422 as not an integer.
@router.get("/search", response_model=SearchResponse)
def search_documents(
    q: str = Query(..., min_length=1, max_length=500, description="Words to search for."),
    limit: int = Query(default=5, ge=1, le=settings.max_search_results),
    # "any" ranks chunks by how many of the words they contain; "all" requires
    # every word. Exposed so the evaluation can compare the two.
    match: Literal["any", "all"] = Query(default="any"),
    current_user: User = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> SearchResponse:
    try:
        hits = service.search(current_user.organization_id, q, limit=limit, match=match)
    except EmptySearchQuery as error:
        raise HTTPException(status_code=422, detail=str(error))
    except StorageError:
        raise _storage_unavailable()
    return SearchResponse(
        query=q,
        results=[SearchResult(**hit.__dict__) for hit in hits],
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentResponse:
    try:
        document = service.get(current_user.organization_id, document_id)
    except StorageError:
        raise _storage_unavailable()
    if document is None:
        # Also the answer for another organisation's document (ADR-015).
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return DocumentResponse.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> None:
    try:
        deleted = service.delete(current_user.organization_id, document_id)
    except StorageError:
        raise _storage_unavailable()
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
