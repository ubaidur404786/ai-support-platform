"""Refuse oversized request bodies before they are read.

Measured in v6: FastAPI parses a multipart body BEFORE it runs any dependency -
including authentication and rate limits. An anonymous 200 MB upload was
received and spooled in full (4.1 s) only to be answered 401. The checks that
protect the server ran after the server had already done the work.

A middleware runs before routing and body parsing, so it can answer from the
headers alone.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Room for the multipart framing and form fields around the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def add_body_size_limit(app: FastAPI, max_body_bytes: int) -> None:
    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                too_large = int(declared) > max_body_bytes
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
            if too_large:
                # Answered without reading a single byte of the body.
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Request body exceeds {max_body_bytes} bytes"},
                )
        # A body sent without Content-Length (chunked transfer) is NOT covered:
        # its size is unknown until it has been read. The endpoint's own check
        # still bounds what is processed, but not what is received. Enforcing
        # that belongs to a reverse proxy (e.g. nginx client_max_body_size) -
        # recorded as a limitation, not solved here.
        return await call_next(request)
