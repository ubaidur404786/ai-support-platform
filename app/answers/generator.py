"""Ask the model service to write an answer.

Since v12 the language model does not run inside the API. It runs in its own
process, the model service (app/model_service/main.py), and this module calls
it over HTTP. The rest of the API does not notice: generate() still returns
the reply, generate_stream() still gives it piece by piece, and every failure
still becomes GenerationUnavailable (503).

What the API gains: an API process no longer holds the 1.1 GB model, so more
API processes fit on one machine and all of them share one model and one queue.
What it costs: a network call per answer (milliseconds next to seconds of
generation), and one more process that can be down.
"""

import json
from collections.abc import Iterator

import httpx

from app.core.config import settings


class GenerationUnavailable(Exception):
    """The model service is down, has no model, or failed. Becomes 503."""


class GenerationBusy(GenerationUnavailable):
    """The model service's queue is full. Becomes 503 with Retry-After."""

    def __init__(self, retry_after: str) -> None:
        super().__init__("The answer model is busy")
        self.retry_after = retry_after


# One client for the whole process: it keeps connections to the model service
# open and reuses them, instead of opening a new one for every answer.
# The read timeout is how long we wait for the next bytes: for /generate that
# is the whole answer, including its time in the service's queue.
_client = httpx.Client(
    base_url=settings.model_service_url,
    timeout=httpx.Timeout(settings.model_service_timeout_seconds, connect=2.0),
)


def _check(response: httpx.Response) -> None:
    """Turn an error response from the model service into our exceptions."""
    if response.status_code == 503 and "Retry-After" in response.headers:
        raise GenerationBusy(response.headers["Retry-After"])
    if response.status_code != 200:
        raise GenerationUnavailable(f"Model service answered {response.status_code}")


def generate(messages: list[dict], max_tokens: int) -> str:
    """Return the model's whole reply to a chat: a list of {"role": ..., "content": ...}."""
    try:
        response = _client.post("/generate", json={"messages": messages, "max_tokens": max_tokens})
    except httpx.HTTPError as error:
        # Not running, not reachable, or took longer than the timeout.
        raise GenerationUnavailable("The model service is unreachable") from error
    _check(response)
    return response.json()["text"]


def generate_stream(messages: list[dict], max_tokens: int) -> Iterator[str]:
    """Start an answer now, then give its pieces as the model writes them.

    Not a generator itself, on purpose: the connection is opened and the status
    checked HERE, when the function is called. So "down" or "busy" is raised
    before the API sends the first line of its own stream, and can still become
    a normal 503. Only the reading of the pieces is left for later.
    """
    request = _client.build_request(
        "POST", "/generate/stream", json={"messages": messages, "max_tokens": max_tokens}
    )
    try:
        # stream=True: return as soon as the status and headers arrive,
        # without waiting for the body.
        response = _client.send(request, stream=True)
    except httpx.HTTPError as error:
        raise GenerationUnavailable("The model service is unreachable") from error
    try:
        _check(response)
    except GenerationUnavailable:
        response.close()
        raise
    return _pieces(response)


def _pieces(response: httpx.Response) -> Iterator[str]:
    try:
        for line in response.iter_lines():
            if not line:
                continue
            event = json.loads(line)
            if "error" in event:
                raise GenerationUnavailable(event["error"])
            yield event["text"]
    except httpx.HTTPError as error:
        raise GenerationUnavailable("The model service stopped mid-answer") from error
    finally:
        # Also runs when our own client disconnects and this generator is
        # closed: the model service sees the connection close and stops writing.
        response.close()
