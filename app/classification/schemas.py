"""Request and response shapes for the classification endpoint."""

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
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be blank")
        return cleaned


class ClassifyResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    label: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    model_version: str