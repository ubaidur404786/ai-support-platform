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
    app_version: str = "0.2.0"
    classifier_path: str = "models/ticket_classifier.joblib"
        # Predictions below this confidence are flagged for a human to review.
    low_confidence_threshold: float = 0.55
        # postgresql+psycopg://user:password@host:port/database
    database_url: str = "postgresql+psycopg://support:support@localhost:5432/support_platform"
    # Connections kept open and reused. Each worker process has its own pool,
    # so workers x (pool_size + max_overflow) must stay under PostgreSQL's limit.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # True prints every SQL statement - useful when learning, noisy otherwise.
    db_echo: bool = False
    log_level: str = "INFO"

    # Read a .env file if present; real environment variables take priority over it.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()