"""The model service: a small HTTP server whose only job is running the language model.

Run it as its own process, next to the API:
    uvicorn app.model_service.main:app --port 8001

Always with ONE worker process: each worker would load its own 1.1 GB copy of
the model, and they would fight over the same CPU cores.

Why a separate process (v12): until v11 every API process loaded the model
itself. One API process used ~2.4 GB, so a second one for more HTTP capacity
did not fit on the 8 GB development machine, and each would have queued its
own answers without knowing about the other. Now the API processes are small
and call this service over HTTP, and this service is the one place that
decides how many answers may run or wait.

Endpoints (called by app/answers/generator.py, not by users):
    GET  /health            is the model loaded? how many answers are queued?
    POST /generate          {"messages": [...], "max_tokens": 200} -> {"text": "..."}
    POST /generate/stream   the same, as NDJSON lines: {"text": "..."} ... or {"error": "..."}
"""

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import configure_logging
from app.model_service import model

logger = logging.getLogger(__name__)


class GenerateRequest(BaseModel):
    messages: list[dict] = Field(min_length=1)
    max_tokens: int = Field(ge=1, le=1000)


# --- The queue limit -------------------------------------------------------------------
#
# The model writes one answer at a time; others wait for the lock. Without a
# limit the wait grows with every new request: at 8 users, the slowest answer
# took 99 s (v12, before this limit). Nobody waits that long for a support
# answer - they give up, and the model still spends the CPU on it.
#
# So at most `model_max_queue` answers may be running or waiting. One more is
# refused at once with 503 "busy", and the API tells the user to retry.
# Refusing early is called backpressure: telling the caller "slow down"
# instead of letting work pile up.

_queue_lock = threading.Lock()
_in_queue = 0  # answers running or waiting right now


def enter_queue() -> bool:
    """Take a place in the queue. False = full, refuse the request."""
    global _in_queue
    with _queue_lock:
        if _in_queue >= settings.model_max_queue:
            return False
        _in_queue += 1
        return True


def leave_queue() -> None:
    global _in_queue
    with _queue_lock:
        _in_queue -= 1


def busy_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="The model is busy",
        headers={"Retry-After": str(settings.model_busy_retry_after_seconds)},
    )


# --- The app ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    # Load at start, not on the first question: the first user should not wait
    # for a 1.1 GB file to be read, and /health should tell the truth at once.
    try:
        model.load(settings.generation_model_path)
    except Exception:
        # Keep running so /health can report the problem (like the API's classifier).
        logger.exception("Could not load the generation model")
    yield


app = FastAPI(title="Model service", version=settings.app_version, lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if model.is_loaded() else "degraded",
        "model_loaded": model.is_loaded(),
        "in_queue": _in_queue,
        "max_queue": settings.model_max_queue,
    }


# Plain "def": the model is CPU work in C++ code, so FastAPI runs it in its
# thread pool and /health stays responsive while an answer is written.
@app.post("/generate")
def generate(payload: GenerateRequest) -> dict:
    if not model.is_loaded():
        raise HTTPException(status_code=503, detail="The model is not loaded")
    if not enter_queue():
        raise busy_error()
    try:
        text = model.generate(payload.messages, payload.max_tokens)
    finally:
        leave_queue()
    return {"text": text}


@app.post("/generate/stream")
def generate_stream(payload: GenerateRequest) -> StreamingResponse:
    # Both checks happen before the response starts, so they keep their 503.
    if not model.is_loaded():
        raise HTTPException(status_code=503, detail="The model is not loaded")
    if not enter_queue():
        raise busy_error()

    def lines() -> Iterator[str]:
        try:
            yield ""  # a starting point, see below
            for piece in model.generate_stream(payload.messages, payload.max_tokens):
                yield json.dumps({"text": piece}) + "\n"
        except Exception:
            logger.exception("Generation failed while streaming")
            yield json.dumps({"error": "Generation failed"}) + "\n"
        finally:
            # Also runs when the caller disconnects halfway: the place is freed.
            leave_queue()

    stream = lines()
    # Run the generator up to its first `yield ""` now. A generator that was
    # never started does NOT run its "finally" when it is thrown away - so if
    # the caller disconnected before the first line, the queue place would be
    # lost for good. Once started, Python always runs "finally" when it is closed.
    next(stream)
    return StreamingResponse(stream, media_type="application/x-ndjson")
