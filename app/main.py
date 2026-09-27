"""Application entry point: builds the app and wires the modules together."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth.router import router as auth_router
from app.classification.classifier import TicketClassifier
from app.classification.router import router as classification_router
from app.core.config import Settings, settings
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimiter, per_minute
from app.core.request_limits import MULTIPART_OVERHEAD_BYTES, add_body_size_limit
from app.documents.router import router as documents_router
from app.health.router import router as health_router

from app.tickets.router import router as tickets_router
logger = logging.getLogger(__name__)


def create_app(app_settings: Settings = settings) -> FastAPI:
    configure_logging(app_settings.log_level)

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
    # Built here, not at import time, so every app instance starts with empty
    # buckets. Tests rely on that: each one gets a fresh app and a fresh budget.
    rate_limiters: dict[str, RateLimiter] = {}
    if app_settings.rate_limit_enabled:
        rate_limiters["auth"] = per_minute(app_settings.auth_rate_limit_per_minute)
        rate_limiters["inference"] = per_minute(
            app_settings.inference_rate_limit_per_minute
        )
        rate_limiters["ingestion"] = per_minute(
            app_settings.ingestion_rate_limit_per_minute
        )
    app.state.rate_limiters = rate_limiters

    # The largest legitimate request is a document upload. Anything bigger is
    # refused from its headers, before authentication has a chance to run.
    add_body_size_limit(
        app, app_settings.max_document_bytes + MULTIPART_OVERHEAD_BYTES
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(classification_router)
    app.include_router(tickets_router)
    app.include_router(documents_router)
    return app


app = create_app()