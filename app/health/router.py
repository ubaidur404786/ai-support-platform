"""Health endpoint: is the service up, and can it do its job?"""

import logging

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import engine
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
    )