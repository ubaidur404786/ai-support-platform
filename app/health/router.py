"""Health endpoint: is the service up, and can it do its job?"""

import logging

import httpx
from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.answers import generator
from app.core.database import engine
from app.documents import embeddings
from app.health.schemas import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def _database_reachable() -> bool:
    try:
        # The cheapest possible query: it proves a connection can be obtained
        # and the server answers, without touching any table.
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        logger.warning("Health check could not reach the database", exc_info=True)
        return False


def _service_status(client: httpx.Client) -> str:
    """Ask another service's /health. "ok", "degraded" (up but no model) or "down".

    A short timeout: /health must answer quickly even when a service hangs.
    """
    try:
        response = client.get("/health", timeout=2.0)
        return response.json()["status"]
    except (httpx.HTTPError, ValueError, KeyError):
        return "down"


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    # Health reads state directly rather than through the dependencies that
    # raise 503, because its job is to report those failures, not to suffer them.
    classifier = request.app.state.classifier
    database_reachable = _database_reachable()

    return HealthResponse(
        # "degraded" means the process is alive but cannot fully serve traffic.
        # This still returns HTTP 200 so a monitoring system can read the detail.
        # Separating liveness (is the process alive?) from readiness (should it
        # receive traffic?) is a deployment concern we address with Kubernetes.
        status="ok" if (classifier and database_reachable) else "degraded",
        model_loaded=classifier is not None,
        model_version=classifier.model_version if classifier else None,
        database_reachable=database_reachable,
        # Reported, but NOT part of "status" (v14). Without the model service
        # the API still logs people in, stores tickets and searches; marking
        # the whole API unhealthy would let one service take down the others.
        model_service=_service_status(generator._client),
        embedding_service=_service_status(embeddings._client),
    )