"""Turn text into embeddings: lists of numbers that describe what the text means.

Keyword search (v6) only finds a chunk that shares WORDS with the question.
"Can I get my money back?" shares no word with "Refunds are issued within ten
business days", so keyword search misses it. An embedding model maps both
sentences to nearby points in a 384-dimensional space, because they mean nearly
the same thing. Closeness between two embeddings is the search score.

Since v13 the model does not run here. It runs in the embedding service
(app/embedding_service/main.py), and this module asks it over HTTP. The API
and the worker both call embed_texts() exactly as before; neither loads the
model any more.
"""

import httpx
import numpy as np

from app.core.config import settings

# How many numbers the model returns per text. The database column is declared
# with this size (pgvector checks it on every insert), so a different model with
# a different size needs a migration, not just a new EMBEDDING_MODEL setting.
EMBEDDING_DIMENSIONS = 384

# Texts per HTTP request. The service refuses more than 64 in one go.
TEXTS_PER_REQUEST = 64


class EmbeddingUnavailable(Exception):
    """The embedding service is down or failed. Becomes 503 in the API."""


# One client per process, so connections to the service are reused.
_client = httpx.Client(
    base_url=settings.embedding_service_url,
    timeout=httpx.Timeout(settings.embedding_service_timeout_seconds, connect=2.0),
)


def embed_texts(texts: list[str], model_name: str = settings.embedding_model) -> np.ndarray:
    """Return one embedding per text, as rows of a float32 array.

    Every vector has length 1 (the model normalises it). For vectors of length
    1, the dot product of two vectors IS their cosine similarity: 1.0 = same
    meaning, around 0 = unrelated.
    """
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    vectors = []
    for start in range(0, len(texts), TEXTS_PER_REQUEST):
        batch = texts[start : start + TEXTS_PER_REQUEST]
        try:
            response = _client.post("/embed", json={"texts": batch, "model": model_name})
        except httpx.HTTPError as error:
            # Not running, not reachable, or too slow. Callers decide what that
            # means: 503 for a search, a retry for the worker.
            raise EmbeddingUnavailable("The embedding service is unreachable") from error
        if response.status_code != 200:
            raise EmbeddingUnavailable(f"Embedding service answered {response.status_code}")
        vectors.extend(response.json()["vectors"])
    return np.array(vectors, dtype=np.float32)
