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
    log_level: str = "INFO"

    # Read a .env file if present; real environment variables take priority over it.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()