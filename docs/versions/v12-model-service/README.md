# v12: Model service

Until v11 the answer model lived inside every API process: 2.4 GB per process, and a line of waiting
questions that nothing limited (99 s for the slowest of 8 simultaneous users). v12 moves the model
into its own small FastAPI process, the **model service**, that the API calls over HTTP. It lets at
most 4 answers run or wait, and refuses the next one at once with `503` and `Retry-After: 10`.

It does not make answers faster: one CPU still writes 5–10 answers a minute. It makes the API small
(~1 GB per process), gives all API processes one shared model and one bounded queue, and logs how
long each answer waited and how long it took to write.

![v12 architecture](../../architecture/v12.svg)

## What this version adds

- `app/model_service/`: `GET /health`, `POST /generate`, `POST /generate/stream` (NDJSON).
- `app/answers/generator.py` is now an HTTP client (httpx) for it.
- A new 503 "busy" case on `/answers` and `/answers/stream`, with `Retry-After`.
- `scripts/load_test_answers.py`: 1/2/4/8 concurrent users.

## Run

Three processes, three terminals (`DB_ECHO=false`, the model downloaded with
`python scripts/download_generation_model.py`):

```bash
uvicorn app.model_service.main:app --port 8001
uvicorn app.main:app --port 8000
python -m app.worker
```

Always **one** model-service process: each one loads its own copy of the model.

## Try it

```bash
curl http://127.0.0.1:8001/health
```

```
{"status": "ok", "model_loaded": true, "in_queue": 0, "max_queue": 4}
```

Stop the model service and ask a question: `POST /answers` returns 503, and
`GET /documents/search?mode=semantic` still works.

## Test

```bash
pytest tests/test_model_service.py tests/test_answers_api.py -v
```

Load test (start the API with `RATE_LIMIT_ENABLED=false`):

```bash
python scripts/load_test_answers.py
```

## Results (measured locally, CPU laptop, 8 GB RAM, ~400 MB free)

| 8 simultaneous users | P50 | P95 | Max | Refused as busy |
|---|---|---|---|---|
| v11, model inside the API | 59.0 s | 93.0 s | 99.1 s | 0 of 24 |
| v12, queue of 4 (run 2) | 32.8 s | 43.6 s | 52.2 s | 12 of 24, in ≤ 1.1 s |
| v12, queue of 4 (run 1) | 29.5 s | 88.8 s | 93.4 s | 12 of 24 |

| Memory (private) | v11 | v12 |
|---|---|---|
| One API process | 2,380 MB | 981 MB (+ model service 1,802 MB) |

Answers per minute: 4–11 in every setup (no speed-up).

## Documents

- Full version document: [v12-model-service.md](../v12-model-service.md)
- [ADR-031: Run the answer model in its own process](../../adr/ADR-031-separate-model-service.md)
- [ADR-032: Bound the model's queue and refuse early](../../adr/ADR-032-bounded-model-queue.md)
- Previous version: [v11: Answer streaming](../v11-answer-streaming.md)
