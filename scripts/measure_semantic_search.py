"""Measure what embeddings cost, in the worker and in search.

Part 1, in-process (no HTTP, no database):
    - how long the model takes to load
    - how long it takes to embed one question
    - how many chunks per second the worker can embed

Part 2, through the running API: search latency for mode=keyword and
mode=semantic as the organisation grows from 1,000 to 50,000 chunks. The chunks
are synthetic and inserted straight into the database with random (but
correctly normalised) vectors, because embedding 50,000 real chunks would take
hours on a laptop CPU. v8's brute force did the same work whatever the numbers
were. The v9 index does not: random vectors have no clusters, which is the
hardest case for it - see scripts/measure_vector_index.py for its recall.

Start the API first, against the database this script will fill:
    DB_ECHO=false uvicorn app.main:app
Then, from the repository root (same DATABASE_URL as the API):
    DB_ECHO=false python scripts/measure_semantic_search.py
"""

import random
import statistics
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from sqlalchemy import insert, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.auth.models  # noqa: E402,F401
import app.tickets.models  # noqa: E402,F401
from app.auth.models import User  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.documents import embeddings  # noqa: E402
from app.documents.embeddings import embed_texts  # noqa: E402
from app.documents.models import READY, Document, DocumentChunk  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
EMAIL = "semantic@measure.example"
PASSWORD = "measure-password-long-enough"
SIZES = [1_000, 10_000, 50_000]
REQUESTS_PER_MODE = 30
QUESTIONS = [
    "How long does a refund take?",
    "Can I get my money back?",
    "How do I reset my password?",
    "Which browsers are supported?",
    "My invoice shows the wrong amount",
]
WORDS = (
    "refund invoice account password billing plan workspace member export ticket "
    "support upload security browser mobile sync outage status role permission "
    "the a to of and is in for with on your you can we our after before within"
).split()


def chunk_text(rng: random.Random) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(120))[:800]


def measure_model() -> None:
    print("Part 1 - the model, in-process")
    start = time.perf_counter()
    embeddings._load_model(settings.embedding_model)
    print(f"  load (first use in a process):  {time.perf_counter() - start:.2f} s")

    timings = []
    for question in QUESTIONS * 6:
        start = time.perf_counter()
        embed_texts([question])
        timings.append((time.perf_counter() - start) * 1000)
    print(f"  embed one question:  P50 {statistics.median(timings):.1f} ms  (n={len(timings)})")

    rng = random.Random()
    texts = [chunk_text(rng) for _ in range(500)]
    start = time.perf_counter()
    embed_texts(texts)
    seconds = time.perf_counter() - start
    print(f"  embed 500 chunks of ~800 chars:  {seconds:.1f} s = {500 / seconds:.0f} chunks/s")


def login(client: httpx.Client) -> int:
    client.post(
        "/auth/register",
        json={"organization_name": "Semantic Measurement", "email": EMAIL, "password": PASSWORD},
    )  # 409 on a second run: the account exists
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    response.raise_for_status()
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    with SessionLocal() as session:
        return session.scalar(select(User.organization_id).where(User.email == EMAIL))


def count_chunks(organization_id: int) -> int:
    with SessionLocal() as session:
        return len(
            session.scalars(
                select(DocumentChunk.id).where(DocumentChunk.organization_id == organization_id)
            ).all()
        )


def add_chunks(organization_id: int, how_many: int, rng: random.Random) -> None:
    """Insert synthetic chunks, 1,000 per document, as if the worker made them."""
    np_rng = np.random.default_rng()
    with SessionLocal() as session:
        user_id = session.scalar(select(User.id).where(User.email == EMAIL))
        while how_many > 0:
            batch = min(1_000, how_many)
            document = Document(
                organization_id=organization_id,
                uploaded_by_user_id=user_id,
                title="Synthetic",
                filename="synthetic.txt",
                content_type="text",
                size_bytes=0,
                sha256=f"{rng.getrandbits(256):064x}",
                status=READY,
                attempts=1,
                chunk_count=batch,
                embedding_model=settings.embedding_model,
            )
            session.add(document)
            session.flush()
            vectors = np_rng.standard_normal((batch, 384)).astype(np.float32)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
            session.execute(
                insert(DocumentChunk),
                [
                    {
                        "document_id": document.id,
                        "organization_id": organization_id,
                        "chunk_index": i,
                        "text": chunk_text(rng),
                        "start_char": 0,
                        "end_char": 800,
                        "embedding": vectors[i],
                    }
                    for i in range(batch)
                ],
            )
            session.commit()
            how_many -= batch


def time_search(client: httpx.Client, mode: str) -> tuple[float, float]:
    timings = []
    for i in range(REQUESTS_PER_MODE):
        question = QUESTIONS[i % len(QUESTIONS)]
        start = time.perf_counter()
        response = client.get("/documents/search", params={"q": question, "mode": mode, "limit": 5})
        timings.append((time.perf_counter() - start) * 1000)
        response.raise_for_status()
    timings.sort()
    return statistics.median(timings), timings[int(len(timings) * 0.95) - 1]


def main() -> None:
    measure_model()

    print("\nPart 2 - search latency through the API (P50 / P95 ms, "
          f"{REQUESTS_PER_MODE} requests per mode)")
    rng = random.Random()
    with httpx.Client(base_url=BASE_URL, timeout=120) as client:
        organization_id = login(client)
        # Untimed: the API process loads the model on its first semantic search.
        start = time.perf_counter()
        client.get("/documents/search", params={"q": "warm up", "mode": "semantic"})
        print(f"  first semantic search after API start (loads the model): "
              f"{(time.perf_counter() - start) * 1000:.0f} ms")

        print(f"  {'chunks':>8}  {'keyword P50':>12} {'P95':>7}  {'semantic P50':>13} {'P95':>7}")
        for size in SIZES:
            existing = count_chunks(organization_id)
            if existing < size:
                add_chunks(organization_id, size - existing, rng)
            keyword = time_search(client, "keyword")
            semantic = time_search(client, "semantic")
            print(f"  {size:>8,}  {keyword[0]:>12.1f} {keyword[1]:>7.1f}  "
                  f"{semantic[0]:>13.1f} {semantic[1]:>7.1f}")


if __name__ == "__main__":
    main()
