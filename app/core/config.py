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
    app_version: str = "0.4.0"
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

    # Read a .env file if present; real environment variables take priority over it.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()