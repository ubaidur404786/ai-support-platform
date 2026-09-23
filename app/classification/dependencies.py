"""How endpoints obtain the classifier.

A "dependency" is a function FastAPI calls for each request; its return value is
passed into the endpoint. Endpoints declare what they need instead of knowing
where it lives, and tests can replace this function with a fake.
"""

from fastapi import HTTPException, Request

from app.classification.classifier import TicketClassifier


def get_classifier(request: Request) -> TicketClassifier:
    classifier = request.app.state.classifier
    if classifier is None:
        # 503 = the API is up but cannot do its job right now.
        raise HTTPException(status_code=503, detail="Model is not loaded")
    return classifier