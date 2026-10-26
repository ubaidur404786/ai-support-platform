"""The embedding service: turns text into vectors for the API and the worker.

Run it as its own process:
    uvicorn app.embedding_service.main:app --port 8002

Why it exists (v13): the API embeds every search question and the worker
embeds every chunk, so both loaded the same model. Measured before this
change: the API process used 975 MB and the worker 556 MB, and the first
semantic search after the API started took 20.9 s because the API loaded the
model on that first request. Now the model is loaded once, here, at start-up.

Endpoints (called by app/documents/embeddings.py, not by users):
    GET  /health   is the model loaded?
    POST /embed    {"texts": ["..."], "model": "..."} -> {"vectors": [[0.01, ...], ...]}
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import configure_logging
from app.embedding_service import model

logger = logging.getLogger(__name__)

# The cap stops one request from keeping the service busy for minutes. The
# client (app/documents/embeddings.py) splits longer lists into several requests.
MAX_TEXTS_PER_REQUEST = 64

_loaded = False


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS_PER_REQUEST)
    # The caller says which model it expects. Vectors from two different
    # models cannot be compared, so a mismatch is an error, not a warning.
    model: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loaded
    configure_logging(settings.log_level)
    try:
        model.load(settings.embedding_model)
        _loaded = True
    except Exception:
        # Keep running so /health can say what is wrong.
        logger.exception("Could not load the embedding model")
    yield


app = FastAPI(title="Embedding service", version=settings.app_version, lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if _loaded else "degraded",
        "model_loaded": _loaded,
        "model": settings.embedding_model,
    }


# Plain "def": embedding is CPU work, so FastAPI runs it in its thread pool.
@app.post("/embed")
def embed(payload: EmbedRequest) -> dict:
    if payload.model != settings.embedding_model:
        raise HTTPException(
            status_code=400,
            detail=f"This service runs {settings.embedding_model!r}, not {payload.model!r}",
        )
    try:
        vectors = model.embed(payload.texts, payload.model)
    except Exception:
        logger.exception("Embedding failed")
        raise HTTPException(status_code=503, detail="The embedding model is unavailable")
    return {"vectors": vectors.tolist()}
