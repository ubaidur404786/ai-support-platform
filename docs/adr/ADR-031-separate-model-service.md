# ADR-031: Run the answer model in its own process, called over HTTP

Status: accepted (v12)

## Context

Since v10 the language model (Qwen2.5-1.5B, a 1.1 GB GGUF file run by llama.cpp) lived inside the
API process, behind a lock. Measured in v12, before any change:

- The API process used **2,380 MB** private memory with the model loaded; the laptop had ~440 MB free.
- Throughput was 4–11 answers a minute whatever the number of users (one CPU, one model).
- With 8 simultaneous users the slowest answer took **99 s**.
- `/health` stayed at ~25 ms P50 during generation (llama.cpp releases the GIL), so the model did
  **not** make the rest of the API slow.

Adding a second API process for HTTP capacity would load a second copy of the model, and each
process would keep its own lock and its own invisible line of waiting requests.

## Options

1. **Keep the model in the API**, run one API process. Simple, but HTTP capacity and model capacity
   stay welded together, and there is no single place to limit the line.
2. **A PostgreSQL-queued worker, like v7's.** Reuses what exists. But an answer is a request someone
   waits for in seconds, and it streams; polling adds delay, and streaming pieces back through a
   table is awkward.
3. **Our own small model service** (FastAPI + llama-cpp-python), called by the API over HTTP.
4. **llama.cpp's own server** (`llama-server`, OpenAI-compatible, can batch several answers).
   Another binary and API format to learn; batching helps little on a CPU where reading the prompt
   dominates.
5. **vLLM / TGI.** Built for GPUs; there is no GPU.

## Decision

Option 3. `app/model_service/main.py` is a separate FastAPI app, run as **one** process
(`uvicorn app.model_service.main:app --port 8001`). It is the only code that loads llama.cpp
(`app/model_service/model.py`, enforced by a test). The API's `app/answers/generator.py` became an
httpx client with the same functions as before, so the rest of the API did not change.

Endpoints: `GET /health` (model loaded, `in_queue`), `POST /generate` → `{"text"}`,
`POST /generate/stream` → NDJSON `{"text"}` lines, or `{"error"}`.

## Why?

- **The two parts need different sizes.** HTTP work is cheap and should scale with traffic; the
  model needs all cores for one answer, so one copy per machine is right on a CPU.
- **One place to decide capacity.** All API processes share one model and one queue (ADR-032).
- **It is the smallest step that separates them.** Same framework, same library, ~250 lines with
  comments. Moving to llama.cpp's server or a GPU engine later only changes this service.

## Trade-offs

Gained: API processes at ~1 GB instead of ~2.4 GB (measured); two API workers fit where they could
not before (3.6 GB in total, measured, vs ~4.8 GB estimated); per-answer timing of wait vs
generation; a visible, bounded queue.

Lost: one more process to run and monitor; a network hop and a timeout (120 s) to choose; slightly
more memory with a single API process (2.8 GB vs 2.4 GB: two Python runtimes). **No speed-up**:
throughput stayed at 5–10 answers a minute, within noise of before.

## Future Trigger

- A GPU, or more than ~10 answers a minute needed: replace the service's inside with a batching
  server (llama.cpp server or vLLM) behind the same `/generate` contract (planned `v15-llm-serving`).
- The model service on another machine: add authentication between the API and the service.
- The API's `/health` should report the model service before any real deployment.
