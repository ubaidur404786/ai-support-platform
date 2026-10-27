"""Application entry point: builds the app and wires the modules together."""

import logging
from contextlib import asynccontextmanager

# redis-py talks to Redis, a small in-memory store shared by every API copy.
import redis
from fastapi import FastAPI

from app.answers.router import router as answers_router
from app.auth.router import router as auth_router
from app.classification.classifier import TicketClassifier
from app.classification.router import router as classification_router
from app.core.config import Settings, settings
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimiter, RedisRateLimiter, per_minute
from app.core.request_limits import MULTIPART_OVERHEAD_BYTES, add_body_size_limit
from app.documents.router import router as documents_router
from app.health.router import router as health_router

from app.tickets.router import router as tickets_router
logger = logging.getLogger(__name__)


def create_app(app_settings: Settings = settings) -> FastAPI:
    configure_logging(app_settings.log_level)
    if len(app_settings.jwt_secret_key) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY is missing or shorter than 32 characters. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )

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
    limits = {
        "auth": app_settings.auth_rate_limit_per_minute,
        "inference": app_settings.inference_rate_limit_per_minute,
        "ingestion": app_settings.ingestion_rate_limit_per_minute,
        "answers": app_settings.answer_rate_limit_per_minute,
    }
    rate_limiters: dict[str, RateLimiter | RedisRateLimiter] = {}
    if app_settings.rate_limit_enabled and app_settings.redis_url:
        # Several API copies (v16): count in Redis so they share one budget.
        # A short timeout, so a slow Redis cannot slow every request down.
        redis_client = redis.Redis.from_url(app_settings.redis_url, socket_timeout=0.5)
        for name, limit in limits.items():
            rate_limiters[name] = RedisRateLimiter(redis_client, name, limit)
    elif app_settings.rate_limit_enabled:
        for name, limit in limits.items():
            rate_limiters[name] = per_minute(limit)
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
    app.include_router(answers_router)
    return app


app = create_app()