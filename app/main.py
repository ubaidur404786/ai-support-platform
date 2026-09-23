"""Application entry point: builds the app and wires the modules together."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.classification.classifier import TicketClassifier
from app.classification.router import router as classification_router
from app.core.config import Settings, settings
from app.core.logging import configure_logging
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
    app.include_router(health_router)
    app.include_router(classification_router)
    app.include_router(tickets_router)
    return app


app = create_app()