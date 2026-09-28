"""Measure what a large upload costs the caller - and everyone else.

Two questions, asked the same way before and after v7:

  1. How long does the person uploading wait for an answer, and how long until
     the document can actually be searched?
  2. While a few large PDFs are being processed, how slow does an ordinary
     request (GET /health: one SELECT 1, no large rows) become for other users?

Works against both versions: v6 answers 201 with a finished document; v7
answers 202 with status "queued", and the script polls until "ready".

Start the API with the upload limit raised, so the measurement is not refused
by its own rate limiter, and with SQL logging off:
    DB_ECHO=false INGESTION_RATE_LIMIT_PER_MINUTE=1000 uvicorn app.main:app
From v7 on, also start the worker in a second terminal:
    DB_ECHO=false python -m app.worker

Then, from the repository root:
    python scripts/measure_async_ingestion.py
"""

import random
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.measure_ingestion import pdf_of_pages, text_of_size  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
EMAIL = "async@measure.example"
PASSWORD = "measure-password-long-enough"
PARALLEL_UPLOADS = 4


def login(client: httpx.Client) -> None:
    client.post(
        "/auth/register",
        json={"organization_name": "Async Measurement", "email": EMAIL, "password": PASSWORD},
    )  # 409 on a second run is fine
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    response.raise_for_status()
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def upload(client: httpx.Client, filename: str, data: bytes) -> tuple[float, dict]:
    """Return (milliseconds until the API answered, response body)."""
    start = time.perf_counter()
    response = client.post("/documents", files={"file": (filename, data, "application/octet-stream")})
    elapsed = (time.perf_counter() - start) * 1000
    if response.status_code not in (201, 202):
        raise SystemExit(f"Upload failed: {response.status_code} {response.text[:200]}")
    return elapsed, response.json()


def wait_until_ready(client: httpx.Client, document: dict) -> None:
    # v6 has no status field: its documents are finished when the upload answers.
    status = document.get("status", "ready")
    while status not in ("ready", "failed"):
        time.sleep(0.05)
        status = client.get(f"/documents/{document['id']}").json()["status"]
    if status == "failed":
        raise SystemExit(f"Document {document['id']} failed")


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def probe(client: httpx.Client, stop: threading.Event, out: list[float]) -> None:
    """Send an ordinary request again and again until told to stop."""
    while not stop.is_set():
        start = time.perf_counter()
        client.get("/health").raise_for_status()
        out.append((time.perf_counter() - start) * 1000)
        time.sleep(0.02)


def main() -> None:
    # Unseeded: every run must produce new files, or the duplicate check answers 409.
    rng = random.Random()
    with httpx.Client(base_url=BASE_URL, timeout=300) as client:
        login(client)

        # --- 1. The uploader's wait ------------------------------------------------
        print(f"{'case':<15}{'answer P50':>12}{'ready P50':>12}   (n=3)")
        cases = [
            ("pdf 300 pages", "doc.pdf", lambda: pdf_of_pages(300, rng)),
            ("txt 4.9 MB", "doc.txt", lambda: f"{uuid.uuid4()}\n\n".encode() + text_of_size(4_900_000, rng).encode()),
        ]
        for label, filename, build in cases:
            answer_ms, ready_ms = [], []
            for _ in range(3):
                data = build()  # built before the clock starts
                start = time.perf_counter()
                elapsed, document = upload(client, filename, data)
                wait_until_ready(client, document)
                answer_ms.append(elapsed)
                ready_ms.append((time.perf_counter() - start) * 1000)
            print(f"{label:<15}{statistics.median(answer_ms):>10.0f}ms{statistics.median(ready_ms):>10.0f}ms")

        # --- 2. Everyone else's wait -----------------------------------------------
        quiet: list[float] = []
        stop = threading.Event()
        worker = threading.Thread(target=probe, args=(client, stop, quiet))
        worker.start()
        time.sleep(5)
        stop.set()
        worker.join()

        pdfs = [pdf_of_pages(300, rng) for _ in range(PARALLEL_UPLOADS)]
        busy: list[float] = []
        stop = threading.Event()
        prober = threading.Thread(target=probe, args=(client, stop, busy))
        prober.start()
        start = time.perf_counter()

        def upload_and_wait(data: bytes) -> None:
            _, document = upload(client, "doc.pdf", data)
            wait_until_ready(client, document)

        uploaders = [threading.Thread(target=upload_and_wait, args=(pdf,)) for pdf in pdfs]
        for thread in uploaders:
            thread.start()
        for thread in uploaders:
            thread.join()
        all_ready_ms = (time.perf_counter() - start) * 1000
        stop.set()
        prober.join()

        print(f"\nGET /health, {len(quiet)} requests with nothing else running:")
        print(f"  P50 {statistics.median(quiet):.1f} ms  P95 {percentile(quiet, 0.95):.1f} ms  max {max(quiet):.1f} ms")
        print(f"GET /health, {len(busy)} requests while {PARALLEL_UPLOADS} x 300-page PDFs were processed:")
        print(f"  P50 {statistics.median(busy):.1f} ms  P95 {percentile(busy, 0.95):.1f} ms  max {max(busy):.1f} ms")
        print(f"  all {PARALLEL_UPLOADS} documents ready after {all_ready_ms:.0f} ms")


if __name__ == "__main__":
    main()
