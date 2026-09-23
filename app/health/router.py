"""Health endpoint: is the service up, and can it do its job?"""

from fastapi import APIRouter, Request

from app.health.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    # Health reads app.state directly (not via get_classifier) because it must
    # answer 200 even when the model is missing - that is exactly what it reports.
    classifier = request.app.state.classifier
    return HealthResponse(
        status="ok",
        model_loaded=classifier is not None,
        model_version=classifier.model_version if classifier else None,
    )