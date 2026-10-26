"""The embedding model itself. Only the embedding service imports this file.

Until v13 this code lived in app/documents/embeddings.py and ran inside both
the API (to embed each search question) and the worker (to embed each chunk),
so the same ~90 MB model, plus ONNX Runtime, was loaded twice.
"""

import logging
from functools import lru_cache

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load(model_name: str):
    """Load the model once and keep it in memory.

    lru_cache remembers the return value: the first call takes seconds, every
    later call returns the same model object at once.
    """
    # fastembed runs the model with ONNX Runtime instead of PyTorch: same
    # vectors, a much smaller install and a much faster import (ADR-023).
    from fastembed import TextEmbedding

    logger.info("Loading embedding model %s", model_name)
    return TextEmbedding(model_name, cache_dir=settings.embedding_cache_dir)


def embed(texts: list[str], model_name: str) -> np.ndarray:
    """One vector of length 1 per text, as rows of a float32 array."""
    model = load(model_name)
    # batch_size = texts per model run (bigger is faster, uses more memory).
    vectors = np.array(list(model.embed(texts, batch_size=32)))
    return vectors.astype(np.float32)
