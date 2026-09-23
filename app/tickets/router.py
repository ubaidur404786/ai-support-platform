"""HTTP endpoints for tickets."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.tickets.dependencies import get_ticket_service
from app.tickets.repository import StorageError
from app.tickets.schemas import CreateTicketRequest, TicketListResponse, TicketResponse
from app.tickets.service import TicketService

logger = logging.getLogger(__name__)

# prefix means every route here starts with /tickets.
router = APIRouter(prefix="/tickets", tags=["tickets"])


# 201 = "created": the correct status for a request that creates a new resource.
@router.post("", response_model=TicketResponse, status_code=201)
def create_ticket(
    payload: CreateTicketRequest,
    service: TicketService = Depends(get_ticket_service),
) -> TicketResponse:
    try:
        ticket = service.submit(payload.text)
    except StorageError:
        # 503, not 500: the request was valid and the caller can retry once the
        # database is back. A 500 would tell them the request itself was broken.
        logger.exception("Storage unavailable while creating a ticket")
        raise HTTPException(status_code=503, detail="Storage is unavailable")
    except Exception:
        logger.exception("Ticket submission failed")
        raise HTTPException(status_code=500, detail="Ticket submission failed")

    # model_validate reads the attributes of our model and builds the response
    # from them (enabled by from_attributes on the schema).
    return TicketResponse.model_validate(ticket)


@router.get("/{ticket_id}", response_model=TicketResponse)
def get_ticket(
    ticket_id: int,
    service: TicketService = Depends(get_ticket_service),
) -> TicketResponse:
    try:
        ticket = service.get(ticket_id)
    except StorageError:
        logger.exception("Storage unavailable while reading a ticket")
        raise HTTPException(status_code=503, detail="Storage is unavailable")

    if ticket is None:
        # The service returned None; turning that into 404 is the router's job.
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")

    return TicketResponse.model_validate(ticket)


@router.get("", response_model=TicketListResponse)
def list_tickets(
    # Query(...) declares optional URL parameters: /tickets?label=billing
    label: str | None = Query(default=None),
    needs_review: bool | None = Query(default=None),
    service: TicketService = Depends(get_ticket_service),
) -> TicketListResponse:
    try:
        tickets = service.list(label=label, needs_review=needs_review)
    except StorageError:
        logger.exception("Storage unavailable while listing tickets")
        raise HTTPException(status_code=503, detail="Storage is unavailable")

    return TicketListResponse(
        total=len(tickets),
        items=[TicketResponse.model_validate(t) for t in tickets],
    )