# ADR-002 — Serve the model inside the API process

Status: accepted (v0)

## Context

The API needs to run model inference on every `/classify` request. The model is a scikit-learn
pipeline that predicts in about 2 ms on CPU. The system is a single FastAPI service with no
other infrastructure.

## Options

1. **Load the model in the API process** at startup and call it directly from the endpoint.
2. **Separate model service**: a second process/container that exposes inference over HTTP or
   gRPC; the API calls it over the network.
3. **Load the model per request** from disk. Simplest code, but pays the file read and
   deserialization on every call.

## Decision

Option 1. The model is loaded once in the FastAPI lifespan (startup hook), stored on
`app.state`, and called from a synchronous endpoint so FastAPI runs it in its thread pool.
If the model file is missing, the server still starts, `/health` reports `model_loaded: false`,
and `/classify` returns HTTP 503.

## Why?

- Inference is 1.7 ms; a network hop to a separate service would cost more than the inference
  itself and add a second component to run, monitor, and fail.
- One process is the simplest thing to reason about and measure.
- The endpoint is a plain `def` (not `async def`), so the CPU-bound prediction does not block
  the event loop that accepts new connections.
- The `TicketClassifier` wrapper is the only thing the API touches, so moving inference out of
  the process later changes one class, not the endpoints.

## Trade-offs

Gain: no network hop, no second deployable, one config, one log stream.

Lose:

- API and model share one process: they cannot be scaled, deployed, or restarted independently.
- Inference is effectively serial. Measured locally: ~80 req/s ceiling regardless of client
  concurrency, and P50 latency rising from 12 ms to 134 ms at concurrency 10 because requests
  queue. Python's GIL prevents the thread pool from running CPU-bound predictions in parallel.
- Model memory is part of the API's memory; a large model would make every API replica heavy.

## Future Trigger

Revisit when:

- A model with inference time in the tens or hundreds of milliseconds replaces the baseline —
  at that point inference, not the HTTP stack, dominates request time.
- Measured throughput under realistic concurrency is insufficient and running multiple uvicorn
  worker processes is not enough (or wastes memory by loading the model N times).
- The model needs a GPU while the API does not.
- Different models need different scaling (classifier vs. embeddings vs. LLM).

Expected next steps in order: multiple worker processes (cheap), then a separate model service
(planned as v9).
