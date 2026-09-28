"""Run the local language model that writes answers.

The model is Qwen2.5-1.5B-Instruct, a small open model trained to follow
instructions, stored as one GGUF file: the model's weights compressed to about
4 bits per number (~1.1 GB instead of ~3 GB), in the format llama.cpp reads.
llama.cpp is a C++ inference engine built to run such models on an ordinary CPU.
"""

import logging
import threading
from functools import lru_cache

from app.core.config import settings

logger = logging.getLogger(__name__)

# How many tokens the model can read and write in one request: the system
# instructions, the sources, the question AND the answer. 3 sources of ~800
# characters (~200 tokens each) plus the rest fit comfortably.
CONTEXT_TOKENS = 2048

# One model object must not be used by two threads at once. FastAPI runs
# endpoints in a thread pool, so two simultaneous questions would do exactly
# that. The lock makes them take turns: the second waits for the first. That
# is the honest capacity of one CPU process (measured in v10).
_generation_lock = threading.Lock()


class GenerationUnavailable(Exception):
    """The model file is missing or the model could not run. Becomes 503."""


@lru_cache(maxsize=1)
def _load_model(model_path: str):
    """Load the model once per process and keep it in memory (like embeddings.py)."""
    # llama-cpp-python: Python bindings for llama.cpp. Imported here so that
    # processes that never generate (the worker, most tests) never load it.
    from llama_cpp import Llama

    logger.info("Loading generation model %s", model_path)
    return Llama(model_path=model_path, n_ctx=CONTEXT_TOKENS, verbose=False)


def generate(messages: list[dict], max_tokens: int, model_path: str = settings.generation_model_path) -> str:
    """Return the model's reply to a chat: a list of {"role": ..., "content": ...}.

    temperature=0 means "always pick the most likely next word": the same
    question and sources give the same answer, which makes answers testable
    and evaluation repeatable. Creativity is not what a support answer needs.
    """
    try:
        model = _load_model(model_path)
        with _generation_lock:
            output = model.create_chat_completion(
                messages=messages, max_tokens=max_tokens, temperature=0
            )
    except Exception as error:
        # A missing file, a corrupt download, out of memory.
        raise GenerationUnavailable("The answer model is unavailable") from error
    return output["choices"][0]["message"]["content"].strip()
