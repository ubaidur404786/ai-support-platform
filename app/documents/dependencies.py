"""Wiring for the documents module."""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_session
from app.documents.repository import DocumentRepository, PostgresDocumentRepository
from app.documents.service import DocumentService


def get_document_repository(
    session: Session = Depends(get_session),
) -> DocumentRepository:
    return PostgresDocumentRepository(session)


def get_document_service(
    repository: DocumentRepository = Depends(get_document_repository),
) -> DocumentService:
    return DocumentService(
        repository=repository,
        max_document_bytes=settings.max_document_bytes,
        max_pending_documents=settings.max_pending_documents_per_organization,
        max_page_size=settings.max_page_size,
        max_search_results=settings.max_search_results,
        embedding_model=settings.embedding_model,
    )
