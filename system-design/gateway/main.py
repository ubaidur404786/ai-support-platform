"""Gateway: the single door to the services.

nginx sends every request to a gateway. The gateway reads the first part
of the path and forwards the request to the service that owns it:

    /api/users/7    ->  USERS_SERVICE_URL/users/7
    /api/tickets/7  ->  TICKETS_SERVICE_URL/tickets/7

The gateway does not know IP addresses. It knows service NAMES (from
environment variables), and Docker's DNS turns a name into an address.
"""

import os
import socket

import httpx
from fastapi import FastAPI, Request, Response

HOST = socket.gethostname()

# Route table: first path segment -> base URL of the service that owns it.
SERVICES = {
    "users": os.environ["USERS_SERVICE_URL"],
    "tickets": os.environ["TICKETS_SERVICE_URL"],
}

# One shared client keeps connections to the services open (no new TCP
# connection per request).
client = httpx.AsyncClient(timeout=10.0, limits=httpx.Limits(max_connections=200))

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok", "gateway": HOST}


@app.api_route("/api/{service}", methods=["GET", "POST"])
@app.api_route("/api/{service}/{path:path}", methods=["GET", "POST"])
async def forward(service: str, request: Request, path: str = ""):
    base_url = SERVICES.get(service)
    if base_url is None:
        return Response(status_code=404, content=b'{"detail":"unknown service"}', media_type="application/json")

    try:
        upstream = await client.request(
            request.method,
            f"{base_url}/{service}/{path}".rstrip("/"),
            content=await request.body(),
            params=request.query_params,
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
    except httpx.RequestError:
        # The service is down or too slow: say so instead of hanging.
        return Response(status_code=502, content=b'{"detail":"service unavailable"}', media_type="application/json")

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type="application/json",
        headers={"X-Gateway": HOST},
    )
