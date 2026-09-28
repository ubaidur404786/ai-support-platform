"""Application configuration.

Settings come from environment variables or a local .env file, never from
hard-coded values, so the same code runs unchanged on a laptop, in Docker,
or on a server.
"""

# pydantic-settings reads typed settings from environment variables / .env files
# and validates them (e.g. a missing or misspelled value fails at startup, not later).
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Support Platform"
    app_version: str = "0.7.0"
    classifier_path: str = "models/ticket_classifier.joblib"

    # Predictions below this confidence are flagged for a human to review.
    low_confidence_threshold: float = 0.55

    # How many tickets a list request returns when the client does not say.
    default_page_size: int = 50
    # The hard ceiling. A client cannot ask for more, because the amount of work
    # the server does must never be chosen by the caller alone.
    max_page_size: int = 200

    # postgresql+psycopg://user:password@host:port/database
    database_url: str = "postgresql+psycopg://support:support@localhost:5432/support_platform"
    # Connections kept open and reused. Each worker process has its own pool,
    # so workers x (pool_size + max_overflow) must stay under PostgreSQL's limit.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # True prints every SQL statement - useful when learning, noisy otherwise.
    db_echo: bool = False
    log_level: str = "INFO"

    # No default on purpose: the application must refuse to start rather than run
    # with a guessable signing key. A forged token is every account at once.
    # Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    jwt_secret_key: str
    # HS256 signs with one shared secret. RS256 signs with a private key and lets
    # other services verify with the public key - worth it when several services
    # must check tokens, which is not yet true here.
    jwt_algorithm: str = "HS256"
    # Short-lived tokens limit the damage of a stolen one. Short enough to matter,
    # long enough that we do not need refresh tokens yet.
    access_token_expire_minutes: int = 60

    # Per-caller limits. False switches every limit off - used to measure the
    # "before" numbers, never meant for a deployment.
    rate_limit_enabled: bool = True
    # Login and registration, per client address: there is no user yet to key
    # on. Each attempt costs ~680 ms of bcrypt, so 10 a minute is still far more
    # than any person typing a password needs.
    auth_rate_limit_per_minute: int = 10
    # Model inference (POST /classify and POST /tickets), per user. One budget for
    # both, because both spend the same resource: the classifier's CPU time.
    inference_rate_limit_per_minute: int = 60
    # Document uploads, per user. Since v7 the request only stores the file, but
    # every upload still becomes work for the worker later.
    ingestion_rate_limit_per_minute: int = 20

    # Documents. Every limit here bounds the work ONE upload can cause (ADR-011).
    # 5 MB is far above a typical help article and well below what would keep a
    # worker busy for a long time. The page limit exists because PDF size and
    # PDF work are not proportional: a small file can hold many pages.
    max_document_bytes: int = 5_000_000
    max_document_pages: int = 300
    # Chunk size in characters. ~800 characters is roughly one or two paragraphs:
    # small enough that a search result is a readable passage, large enough to
    # keep a sentence with its context. A starting guess, to be evaluated.
    chunk_max_chars: int = 800
    # Characters repeated between neighbouring chunks, so a sentence cut at a
    # boundary still appears whole in one of them.
    chunk_overlap_chars: int = 100
    # Upper bound on search results per request, for the same reason as
    # max_page_size.
    max_search_results: int = 20

    # Background processing (v7). The worker is a separate process
    # (python -m app.worker) that turns queued uploads into searchable chunks.
    #
    # How long the worker sleeps when there is nothing to do. It is also the
    # longest a new upload waits before the worker notices it.
    worker_poll_seconds: float = 1.0
    # A document still "processing" after this long belonged to a worker that
    # died (crash, killed, machine restarted). It is put back in the queue. Must
    # be comfortably longer than the slowest real document (~8 s measured).
    worker_stale_after_seconds: int = 300
    # Tries per document before it is marked failed. Only unexpected errors are
    # retried: an unreadable file will be just as unreadable the next time.
    worker_max_attempts: int = 3
    # Documents one organisation may have waiting at once. The rate limit bounds
    # uploads per minute; this bounds the queue itself, so one organisation
    # cannot bury everyone else's uploads under its own.
    max_pending_documents_per_organization: int = 50

    # Read a .env file if present; real environment variables take priority over it.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()