"""The language model that writes answers, loaded once in the model service.

The model is Qwen2.5-1.5B-Instruct, a small open model trained to follow
instructions, stored as one GGUF file: the model's weights compressed to about
4 bits per number (~1.1 GB instead of ~3 GB), in the format llama.cpp reads.
llama.cpp is a C++ inference engine built to run such models on an ordinary CPU.

Until v11 this code ran inside every API process. Since v12 only the model
service (app/model_service/main.py) imports it, so there is exactly one copy of
the model on the machine, however many API processes there are.
"""

import logging
import threading
import time
from collections.abc import Iterator

logger = logging.getLogger(__name__)

# How many tokens the model can read and write in one request: the system
# instructions, the sources, the question AND the answer. 3 sources of ~800
# characters (~200 tokens each) plus the rest fit comfortably.
CONTEXT_TOKENS = 2048

# One model object must not be used by two threads at once, and FastAPI runs
# endpoints in a thread pool. The lock makes requests take turns. It is also
# the honest capacity of one CPU: two answers at once would each take twice as
# long, because both need all the cores (measured in v10 and v12).
_generation_lock = threading.Lock()

# The loaded model, or None until load() succeeds.
_model = None


class ModelNotLoaded(Exception):
    """load() failed or was never called. The service answers 503."""


def load(model_path: str) -> None:
    """Load the model into memory. Called once, when the service starts."""
    global _model
    # llama-cpp-python: Python bindings for llama.cpp. Imported here so that
    # importing this module (in tests) does not need the library loaded.
    from llama_cpp import Llama

    logger.info("Loading generation model %s", model_path)
    _model = Llama(model_path=model_path, n_ctx=CONTEXT_TOKENS, verbose=False)


def is_loaded() -> bool:
    return _model is not None


def generate(messages: list[dict], max_tokens: int) -> str:
    """Return the model's reply to a chat: a list of {"role": ..., "content": ...}.

    temperature=0 means "always pick the most likely next word": the same
    question and sources give the same answer, which makes answers testable
    and evaluation repeatable. Creativity is not what a support answer needs.
    """
    if _model is None:
        raise ModelNotLoaded()
    arrived = time.perf_counter()
    with _generation_lock:
        started = time.perf_counter()
        output = _model.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=0)
    log_timing(arrived, started)
    return output["choices"][0]["message"]["content"].strip()


def generate_stream(messages: list[dict], max_tokens: int) -> Iterator[str]:
    """Like generate(), but yield the reply in small pieces as the model writes them.

    The lock is held for the whole answer. If the client disconnects, Python
    closes this generator, and the "with" block releases the lock.
    """
    if _model is None:
        raise ModelNotLoaded()
    arrived = time.perf_counter()
    with _generation_lock:
        started = time.perf_counter()
        chunks = _model.create_chat_completion(
            messages=messages, max_tokens=max_tokens, temperature=0, stream=True
        )
        for chunk in chunks:
            # Each chunk holds the newly written text under "delta"; the first
            # and last ones carry no text, only bookkeeping.
            piece = chunk["choices"][0]["delta"].get("content")
            if piece:
                yield piece
        log_timing(arrived, started)


def log_timing(arrived: float, started: float) -> None:
    """One log line per answer: time spent waiting for the model, and using it.

    The two numbers answer different questions. A long wait means too many
    requests for one model (a capacity problem); a long generation means the
    model itself is slow (a hardware or prompt-size problem).
    """
    now = time.perf_counter()
    logger.info("answer: waited %.1f s for the model, generated in %.1f s", started - arrived, now - started)
