"""HTTP endpoint for generated answers."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.answers.generator import GenerationUnavailable
from app.answers.schemas import AnswerRequest, AnswerResponse, AnswerSource
from app.answers.service import AnswerService
from app.auth.dependencies import get_current_user, limit_answers
from app.auth.models import User
from app.core.config import settings
from app.core.errors import StorageError
from app.documents.dependencies import get_document_service
from app.documents.embeddings import EmbeddingUnavailable
from app.documents.service import DocumentService, EmptySearchQuery

logger = logging.getLogger(__name__)

router = APIRouter(tags=["answers"])


def get_answer_service(
    documents: DocumentService = Depends(get_document_service),
) -> AnswerService:
    return AnswerService(
        documents=documents,
        relevance_threshold=settings.answer_relevance_threshold,
        max_sources=settings.answer_max_sources,
        max_tokens=settings.answer_max_tokens,
    )


# Plain "def": generation is CPU work in C++ code, so FastAPI runs it in its
# thread pool and the event loop stays free for other requests (/health).
# The caller waits for the whole answer - seconds on a CPU (measured in v10).
@router.post("/answers", response_model=AnswerResponse)
def answer_question(
    payload: AnswerRequest,
    current_user: User = Depends(get_current_user),
    _: None = Depends(limit_answers),
    service: AnswerService = Depends(get_answer_service),
) -> AnswerResponse:
    try:
        answer = service.answer(current_user.organization_id, payload.question)
    except EmptySearchQuery as error:
        raise HTTPException(status_code=422, detail=str(error))
    except EmbeddingUnavailable:
        logger.exception("Embedding model unavailable")
        raise HTTPException(status_code=503, detail="Answers are unavailable: search model not loaded")
    except GenerationUnavailable:
        logger.exception("Generation model unavailable")
        # Search still works; the client can fall back to showing search results.
        raise HTTPException(
            status_code=503,
            detail="Answers are unavailable; try GET /documents/search?mode=semantic",
        )
    except StorageError:
        logger.exception("Storage failure while answering")
        raise HTTPException(status_code=503, detail="Storage is unavailable")

    return AnswerResponse(
        question=payload.question,
        answer=answer.text,
        answered=answer.answered,
        reason=answer.reason,
        sources=[
            AnswerSource(
                number=number,
                document_id=hit.document_id,
                document_title=hit.document_title,
                chunk_index=hit.chunk_index,
                score=round(hit.rank, 4),
                text=hit.text,
            )
            for number, hit in enumerate(answer.sources, start=1)
        ],
    )
