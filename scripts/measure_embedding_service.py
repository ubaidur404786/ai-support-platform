"""Time semantic search from the outside, as a user would see it.

Used in v13 to compare "embedding model inside the API and the worker" with
"embedding model in its own service". Start the processes first (see the v13
version document), then:

    python scripts/measure_embedding_service.py

It uploads one help article, waits until the worker has processed it, then
times the FIRST semantic search after the API started and 20 more after it.
Process memory is read separately (Task Manager or Get-Process), because the
script cannot know which processes you started.
"""

import statistics
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000"
EMAIL = "embedding-measure@example.com"
PASSWORD = "measure-password-long-enough"
ARTICLE = Path("evaluation/knowledge_base/refund-policy.md")


def login(client: httpx.Client) -> None:
    client.post(
        "/auth/register",
        json={"organization_name": "Embedding Measurement", "email": EMAIL, "password": PASSWORD},
    )  # 409 on a second run: the account already exists, which is fine
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    response.raise_for_status()
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def unique_copy() -> bytes:
    # The API refuses an identical file twice (409), so each run adds a line.
    return ARTICLE.read_bytes() + f"\n\nMeasured at {time.time()}\n".encode()


def upload_and_wait(client: httpx.Client) -> float:
    start = time.perf_counter()
    response = client.post(
        "/documents", files={"file": (ARTICLE.name, unique_copy(), "text/markdown")}
    )
    response.raise_for_status()
    document_id = response.json()["id"]
    while True:
        status = client.get(f"/documents/{document_id}").json()["status"]
        if status in ("ready", "failed"):
            print(f"  document {document_id}: {status}")
            return time.perf_counter() - start
        time.sleep(0.2)


def search(client: httpx.Client) -> float:
    start = time.perf_counter()
    response = client.get("/documents/search", params={"q": "Can I get my money back?", "mode": "semantic"})
    response.raise_for_status()
    return (time.perf_counter() - start) * 1000


def main() -> None:
    with httpx.Client(base_url=API, timeout=300) as client:
        login(client)
        first = search(client)
        print(f"  first semantic search after start: {first:.0f} ms")
        later = [search(client) for _ in range(20)]
        print(f"  next 20 searches: P50 {statistics.median(later):.0f} ms, max {max(later):.0f} ms")
        print(f"  upload -> processed: {upload_and_wait(client):.2f} s")


if __name__ == "__main__":
    main()
