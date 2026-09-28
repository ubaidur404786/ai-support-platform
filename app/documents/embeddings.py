"""Turn text into embeddings: lists of numbers that describe what the text means.

Keyword search (v6) only finds a chunk that shares WORDS with the question.
"Can I get my money back?" shares no word with "Refunds are issued within ten
business days", so keyword search misses it. An embedding model maps both
sentences to nearby points in a 384-dimensional space, because they mean nearly
the same thing. Closeness between two embeddings is the search score.

The model runs locally on the CPU. It is downloaded once (~90 MB) on first use
into EMBEDDING_CACHE_DIR; after that no network is needed.
"""

import logging
from functools import lru_cache

# NumPy stores the vectors as compact arrays of numbers and does the maths on
# them - comparing a question with thousands of chunks is one matrix product.
import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmbeddingUnavailable(Exception):
    """The model could not be loaded or could not run. Becomes 503 in the API."""


@lru_cache(maxsize=1)
def _load_model(model_name: str):
    """Load the model once per process and keep it in memory.

    lru_cache remembers the return value: the first call takes seconds, every
    later call returns the same model object at once.
    """
    # fastembed runs the model with ONNX Runtime, a small inference engine,
    # instead of PyTorch. Same model, same vectors - but sentence-transformers
    # (PyTorch) took ~125 s to import and load on the development laptop against
    # ~28 s here, and PyTorch is a far bigger install (ADR-023).
    #
    # Imported here, not at the top of the file, so a process that never embeds
    # anything (a migration, the ticket tests) does not pay for the import.
    from fastembed import TextEmbedding

    logger.info("Loading embedding model %s", model_name)
    return TextEmbedding(model_name, cache_dir=settings.embedding_cache_dir)


def embed_texts(texts: list[str], model_name: str = settings.embedding_model) -> np.ndarray:
    """Return one embedding per text, as rows of a float32 array.

    Every vector has length 1 (the model normalises it). For vectors of length
    1, the dot product of two vectors IS their cosine similarity: 1.0 = same
    meaning, around 0 = unrelated. That keeps the search maths to one product.
    """
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    try:
        model = _load_model(model_name)
        # embed() yields one vector per text; batch_size = texts per model run
        # (bigger is faster, uses more memory).
        vectors = np.array(list(model.embed(texts, batch_size=32)))
    except Exception as error:
        # A missing download, a corrupt cache, out of memory. Callers decide
        # what that means: 503 for a search, a retry for the worker.
        raise EmbeddingUnavailable(f"Embedding model {model_name!r} is unavailable") from error
    return vectors.astype(np.float32)


# How a vector is stored in PostgreSQL: its 384 float32 numbers packed into
# 384 x 4 = 1,536 bytes (a BYTEA column). Reading them back is a memory copy,
# not a conversion of 384 separate numbers - that matters when a search reads
# every chunk of an organisation.


def to_bytes(vector: np.ndarray) -> bytes:
    return vector.astype(np.float32).tobytes()


def from_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)
