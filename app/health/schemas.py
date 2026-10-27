"""Response shape for the health endpoint."""

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    status: str
    model_loaded: bool
    model_version: str | None = None
    database_reachable: bool
    # "ok", "degraded" or "down" (v14)
    model_service: str
    embedding_service: str