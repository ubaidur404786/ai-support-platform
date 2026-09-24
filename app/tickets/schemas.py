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
    """One page of tickets.

    The v1 decision to return an object rather than a bare list pays off here:
    limit and offset were added without breaking any existing client, which a
    top-level JSON array could not have done.
    """

    # How many tickets match the filters in total - not how many are in `items`.
    total: int
    # Echoed back so a client always knows which window it received, even when
    # it sent no parameters and the server chose the defaults.
    limit: int
    offset: int
    items: list[TicketResponse]