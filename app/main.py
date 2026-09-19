"""FastAPI application: the HTTP layer in front of the ticket classifier."""

import logging
from contextlib import asynccontextmanager

# FastAPI builds the HTTP API: routing, request validation, and interactive docs at /docs.
# APIRouter groups endpoints so they can be attached to any app instance.
from fastapi import APIRouter, FastAPI, HTTPException, Request

from app.classifier import TicketClassifier
from app.config import Settings, settings
from app.schemas import ClassifyRequest, ClassifyResponse, HealthResponse

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    classifier = request.app.state.classifier
    return HealthResponse(
        status="ok",
        model_loaded=classifier is not None,
        model_version=classifier.model_version if classifier else None,
    )


# Plain "def", not "async def": model prediction is CPU work that blocks while it
# runs. FastAPI executes sync endpoints in a thread pool, so the server can keep
# accepting other requests meanwhile.
@router.post("/classify", response_model=ClassifyResponse)
def classify(payload: ClassifyRequest, request: Request) -> ClassifyResponse:
    classifier = request.app.state.classifier
    if classifier is None:
        # 503 = "service unavailable": the API is up but cannot do its job right now.
        raise HTTPException(status_code=503, detail="Model is not loaded")

    try:
        prediction = classifier.predict(payload.text)
    except Exception:
        # Log the full traceback for us; return a generic message to the client.
        logger.exception("Prediction failed")
        raise HTTPException(status_code=500, detail="Prediction failed")

    return ClassifyResponse(
        label=prediction.label,
        confidence=prediction.confidence,
        model_version=prediction.model_version,
    )


def create_app(app_settings: Settings = settings) -> FastAPI:
    """Build a FastAPI app from explicit settings.

    A factory (instead of one module-level app) lets tests build isolated apps
    with different settings without sharing state.
    """

    # A "lifespan" runs code once when the server starts (before yield) and once
    # when it stops (after yield). Loading the model here means one disk read at
    # startup instead of one per request.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.classifier = TicketClassifier.load(app_settings.classifier_path)
        except FileNotFoundError as error:
            # Start anyway so /health can report the problem instead of the process dying.
            app.state.classifier = None
            logger.error("%s", error)
        yield
        app.state.classifier = None

    app = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


# The instance uvicorn runs: "uvicorn app.main:app"
app = create_app()