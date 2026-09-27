"""Measure how long an upload keeps a request busy, by file size and type.

Two views of the same work:
  in-process - extract_text() and chunk_text() timed directly, no HTTP, no DB
  end-to-end - POST /documents through the running API, including the insert

Start the server with the ingestion limit raised, so the measurement is not
refused by its own rate limiter:
    INGESTION_RATE_LIMIT_PER_MINUTE=1000 uvicorn app.main:app

Then, from the repository root:
    python scripts/measure_ingestion.py
"""

import random
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.documents.chunking import chunk_text  # noqa: E402
from app.documents.extraction import extract_text  # noqa: E402
from tests.pdf_factory import make_pdf  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
EMAIL = "ingestion@measure.example"
PASSWORD = "measure-password-long-enough"
WORDS = (
    "refund invoice account password billing plan workspace member export ticket "
    "support upload security browser mobile sync outage status role permission "
    "the a to of and is in for with on your you can we our after before within"
).split()


def sentence(rng: random.Random) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(8, 16))).capitalize() + "."


def text_of_size(n_bytes: int, rng: random.Random) -> str:
    parts, size = [], 0
    while size < n_bytes:
        paragraph = " ".join(sentence(rng) for _ in range(4))
        parts.append(paragraph)
        size += len(paragraph) + 2
    return "\n\n".join(parts)[:n_bytes]


def pdf_of_pages(pages: int, rng: random.Random) -> bytes:
    # ~45 lines of ~60 characters: roughly a dense A4 page of prose.
    page_texts = []
    for _ in range(pages):
        words = text_of_size(2700, rng).replace("\n\n", " ").split()
        lines, line = [], ""
        for word in words:
            if len(line) + len(word) > 60:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        lines.append(line)
        page_texts.append("\n".join(lines))
    return make_pdf(page_texts)


CASES = [
    ("txt 10 KB", "doc.txt", lambda rng: text_of_size(10_000, rng).encode(), 5),
    ("txt 1 MB", "doc.txt", lambda rng: text_of_size(1_000_000, rng).encode(), 5),
    ("txt 4.9 MB", "doc.txt", lambda rng: text_of_size(4_900_000, rng).encode(), 3),
    ("pdf 10 pages", "doc.pdf", lambda rng: pdf_of_pages(10, rng), 5),
    ("pdf 100 pages", "doc.pdf", lambda rng: pdf_of_pages(100, rng), 3),
    ("pdf 300 pages", "doc.pdf", lambda rng: pdf_of_pages(300, rng), 3),
]


def main() -> None:
    rng = random.Random(6)
    with httpx.Client(base_url=BASE_URL, timeout=300) as client:
        client.post(
            "/auth/register",
            json={"organization_name": "Ingestion Measurement", "email": EMAIL, "password": PASSWORD},
        )
        login = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        login.raise_for_status()
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

        print(f"{'case':<15}{'bytes':>10}{'chunks':>8}{'extract':>10}{'chunk':>9}{'HTTP P50':>10}  (n, min-max)")
        for label, filename, build, n in CASES:
            extract_ms, chunk_ms, http_ms = [], [], []
            for _ in range(n):
                # A unique marker per upload, so the duplicate check never fires.
                data = build(rng)
                if filename.endswith(".txt"):
                    data = f"{uuid.uuid4()}\n\n".encode() + data[:-37]

                start = time.perf_counter()
                extracted = extract_text(filename, data, 300)
                extract_ms.append((time.perf_counter() - start) * 1000)
                start = time.perf_counter()
                chunks = chunk_text(extracted.text)
                chunk_ms.append((time.perf_counter() - start) * 1000)

                start = time.perf_counter()
                response = client.post("/documents", files={"file": (filename, data, "application/octet-stream")})
                http_ms.append((time.perf_counter() - start) * 1000)
                if response.status_code != 201:
                    raise SystemExit(f"{label}: {response.status_code} {response.text[:200]}")

            print(
                f"{label:<15}{len(data):>10}{len(chunks):>8}"
                f"{statistics.median(extract_ms):>9.0f}ms{statistics.median(chunk_ms):>7.0f}ms"
                f"{statistics.median(http_ms):>8.0f}ms  (n={n}, {min(http_ms):.0f}-{max(http_ms):.0f})"
            )


if __name__ == "__main__":
    main()
