"""Request and response shapes for the tickets endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateTicketRequest(BaseModel):
    text: str = Field(
        ...,
        min_length=3,
        max_length=5000,
        examples=["I was charged twice for my subscription this month"],
    )

    @field_validator("text")
    @classmethod
    def strip_and_reject_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be blank")
        return cleaned


class TicketResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=(),from_attributes=True)

    id: int
    text: str
    label: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    model_version: str
    needs_review: bool
    created_at: datetime


class TicketListResponse(BaseModel):
    # Returning an object rather than a bare list leaves room to add paging
    # fields later without breaking clients.
    total: int
    items: list[TicketResponse]