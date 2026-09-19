
"""Request and response shapes for the API."""

# Pydantic validates and structures API input/output data. FastAPI uses these
# classes to reject bad requests automatically and to generate the API docs.
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClassifyRequest(BaseModel):
    text: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        examples=["I was charged twice for my subscription this month"],
    )

    @field_validator("text")
    @classmethod
    def strip_and_reject_blank(cls, value: str) -> str:
        # min_length counts spaces, so "   " would pass without this extra check.
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be blank")
        return cleaned


class ClassifyResponse(BaseModel):
    # Pydantic reserves the "model_" prefix for its own methods and warns about
    # fields like model_version; this line disables that warning.
    model_config = ConfigDict(protected_namespaces=())

    label: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    model_version: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    status: str
    model_loaded: bool
    model_version: str | None = None