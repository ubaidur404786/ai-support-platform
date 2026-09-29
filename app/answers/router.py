"""HTTP endpoints for generated answers."""

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.answers.generator import GenerationBusy, GenerationUnavailable
from app.answers.schemas import AnswerRequest, AnswerResponse, AnswerSource
from app.answers.service import AnswerService
from app.auth.dependencies import get_current_user, limit_answers
from app.auth.models import User
from app.core.config import settings
from app.core.errors import StorageError
from app.documents.dependencies import get_document_service
from app.documents.embeddings import EmbeddingUnavailable
from app.documents.repository import SearchHit
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


def to_http_error(error: Exception) -> HTTPException:
    """The same status codes for both endpoints."""
    if isinstance(error, EmptySearchQuery):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, EmbeddingUnavailable):
        logger.exception("Embedding model unavailable")
        return HTTPException(status_code=503, detail="Answers are unavailable: search model not loaded")
    if isinstance(error, GenerationBusy):
        # Every answer place in the model service is taken. Not a failure:
        # the same question will very likely work a few seconds later.
        return HTTPException(
            status_code=503,
            detail="The answer model is busy; try again shortly",
            headers={"Retry-After": error.retry_after},
        )
    if isinstance(error, GenerationUnavailable):
        logger.exception("Generation model unavailable")
        # Search still works; the client can fall back to showing search results.
        return HTTPException(
            status_code=503,
            detail="Answers are unavailable; try GET /documents/search?mode=semantic",
        )
    logger.exception("Storage failure while answering")
    return HTTPException(status_code=503, detail="Storage is unavailable")


# The errors to_http_error knows. Anything else is a bug and stays a 500.
ANSWER_ERRORS = (EmptySearchQuery, EmbeddingUnavailable, GenerationUnavailable, StorageError)


def to_sources(hits: list[SearchHit]) -> list[AnswerSource]:
    return [
        AnswerSource(
            number=number,
            document_id=hit.document_id,
            document_title=hit.document_title,
            chunk_index=hit.chunk_index,
            score=round(hit.rank, 4),
            text=hit.text,
        )
        for number, hit in enumerate(hits, start=1)
    ]


# Plain "def": waiting for the model service is a blocking HTTP call (httpx's
# normal client), so FastAPI runs it in its thread pool and the event loop
# stays free for other requests (/health). The caller waits for the whole
# answer - seconds on a CPU (measured in v10).
@router.post("/answers", response_model=AnswerResponse)
def answer_question(
    payload: AnswerRequest,
    current_user: User = Depends(get_current_user),
    _: None = Depends(limit_answers),
    service: AnswerService = Depends(get_answer_service),
) -> AnswerResponse:
    try:
        answer = service.answer(current_user.organization_id, payload.question)
    except ANSWER_ERRORS as error:
        raise to_http_error(error)

    return AnswerResponse(
        question=payload.question,
        answer=answer.text,
        answered=answer.answered,
        reason=answer.reason,
        sources=to_sources(answer.sources),
    )


@router.post("/answers/stream")
def stream_answer(
    payload: AnswerRequest,
    current_user: User = Depends(get_current_user),
    _: None = Depends(limit_answers),
    service: AnswerService = Depends(get_answer_service),
) -> StreamingResponse:
    """The same answer as POST /answers, sent while it is being written (v11).

    The body is NDJSON ("newline-delimited JSON"): one JSON object per line,
    each line sent as soon as it exists. A client reads line by line:

        {"type": "sources", "sources": [...]}        first, at once (if any)
        {"type": "text", "text": "Workspace owners"}  many, as the model writes
        {"type": "done", "answered": true, "reason": "answered", "answer": "..."}

    The last line always says whether this counts as an answer.
    """
    # Everything that can fail with a clear status code happens BEFORE the
    # first line is sent. After that the response is already "200 OK", and a
    # failure can only be reported inside the stream.
    try:
        sources = service.retrieve(current_user.organization_id, payload.question)
        events = service.open_stream(payload.question, sources)
    except ANSWER_ERRORS as error:
        raise to_http_error(error)

    def lines() -> Iterator[str]:
        if sources:
            event = {"type": "sources", "sources": [s.model_dump() for s in to_sources(sources)]}
            yield json.dumps(event) + "\n"
        try:
            for event in events:
                yield json.dumps(event) + "\n"
        except Exception:
            logger.exception("Generation failed while streaming")
            yield json.dumps({"type": "error", "detail": "The answer could not be finished"}) + "\n"

    # A plain (non-async) generator: Starlette runs it in its thread pool, one
    # next() at a time, so the event loop is never blocked while it waits.
    return StreamingResponse(lines(), media_type="application/x-ndjson")
