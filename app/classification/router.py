"""HTTP endpoint for direct classification."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.classification.classifier import TicketClassifier
from app.classification.dependencies import get_classifier
from app.classification.schemas import ClassifyRequest, ClassifyResponse

logger = logging.getLogger(__name__)

# tags group endpoints under a heading in the generated /docs page.
router = APIRouter(tags=["classification"])


# Plain "def": prediction is CPU-bound, so FastAPI runs it in a thread pool.
@router.post("/classify", response_model=ClassifyResponse)
def classify(
    payload: ClassifyRequest,
    # Depends(...) asks FastAPI to call get_classifier and hand us the result.
    classifier: TicketClassifier = Depends(get_classifier),
) -> ClassifyResponse:
    try:
        prediction = classifier.predict(payload.text)
    except Exception:
        logger.exception("Prediction failed")
        raise HTTPException(status_code=500, detail="Prediction failed")

    return ClassifyResponse(
        label=prediction.label,
        confidence=prediction.confidence,
        model_version=prediction.model_version,
    )