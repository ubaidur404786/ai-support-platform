# AI Support Platform

An AI-powered support and knowledge automation platform, built as one continuously evolving
system. Each version starts from a measured limitation of the previous one and introduces the
smallest architectural change that solves it.

**Current version: v0 — Baseline** (branch `v0-baseline`)

## 1. Project Overview

The platform receives support requests, classifies them, and — in later versions — searches an
organizational knowledge base, generates grounded answers, and performs controlled actions such
as creating or updating tickets.

The engineering goal is a production-style AI system whose every component can be justified:
what problem forced it in, what it costs, and what happens when it fails. The architecture
history is preserved as branches (`v0-baseline`, `v1-...`) and documented in
[`docs/versions/`](docs/versions/) and [`docs/adr/`](docs/adr/).

## 2. Problem Being Solved

Support teams receive a stream of free-text requests that someone has to read and route to the
right team. That work is repetitive, slow, and error-prone. The first capability is automatic
routing: given the text of a ticket, return its category with a confidence score.

## 3. Main AI Capabilities

| Capability | Status | Since |
|---|---|---|
| Ticket classification (billing / technical issue / account access / feature request) | Available | v0 |
| Knowledge base search and retrieval-augmented answers | Planned | — |
| Controlled actions (create / update tickets) with human approval | Planned | — |
| Model evaluation, monitoring, and safe rollout | Planned | — |

## 4. Current Architecture

![v0 architecture](docs/architecture/v0.svg)

One FastAPI process. A TF-IDF + Logistic Regression classifier is loaded once at startup and
runs inside the API process. There is no database, cache, queue, or external service.

```
Client → FastAPI → Ticket classifier (in-process) → JSON response
```

## 5. Request Flow

1. Client sends `POST /classify` with `{"text": "..."}`.
2. Pydantic validates the body: required, 3–2000 characters, not blank. Invalid → HTTP 422.
3. The endpoint reads the loaded classifier from application state. Not loaded → HTTP 503.
4. `TicketClassifier.predict(text)` runs in FastAPI's thread pool (the endpoint is synchronous
   because prediction is CPU-bound).
5. Response: `{"label": "billing", "confidence": 0.72, "model_version": "v0.1.0"}`.
   An unexpected exception during prediction → HTTP 500 with a generic message; the traceback
   goes to the server log.

`GET /health` reports `{"status": "ok", "model_loaded": true|false, "model_version": ...}`.

## 6. AI Flow

Offline (before the server starts):

```
ml/data/tickets.csv  →  ml/train.py  →  models/ticket_classifier.joblib + models/metrics.json
   200 labelled            TF-IDF + Logistic        pipeline + model_version + labels
   tickets                 Regression, 75/25         + trained_at + sklearn_version
                           held-out evaluation
```

Online (per request):

```
text → TF-IDF features (1–3 grams) → Logistic Regression → probabilities → argmax
                                                            → label + confidence
```

## 7. Data Flow

Nothing is persisted in v0. The request body is validated, classified, and forgotten. The only
stored artifacts are the model file and its metrics, produced at training time.

## 8. Technology Stack

| Layer | Technology | Why (short) |
|---|---|---|
| Language | Python 3.12 | Standard for AI engineering |
| API | FastAPI + Uvicorn | Validation, generated docs, sync endpoints in a thread pool |
| Validation / config | Pydantic, pydantic-settings | Typed request/response models; settings from environment variables |
| Model | scikit-learn (TF-IDF + Logistic Regression), joblib | Kilobyte-sized, ~2 ms CPU inference, standard metrics |
| Tests | pytest, httpx | In-process API tests including failure cases |
| Container | Docker | Reproducible runtime; model trained inside the image |

Dependencies: `requirements.in` lists direct dependencies; `requirements.txt` is the frozen,
pinned set used for installs and the Docker build.

## 9. Current Version

**v0 — Baseline.** See [docs/versions/v0-baseline.md](docs/versions/v0-baseline.md) for the
full problem / solution / trade-off / measurement write-up, and
[docs/versions/v0-baseline/README.md](docs/versions/v0-baseline/README.md) for a short guide.

## 10. Version Evolution

| Version | Branch | Problem it solves | Status |
|---|---|---|---|
| v0 | `v0-baseline` | A support ticket needs to be classified over HTTP with a free, local model | Complete |
| v1 | `v1-modular-monolith` | Upcoming features do not fit a flat single-router layout | Next |

Later versions are added here as each is completed. Old branches remain as engineering history.

## 11. How to Run

Requirements: Python 3.11 or 3.12, or Docker.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python ml/train.py               # produces models/ticket_classifier.joblib
uvicorn app.main:app
```

Interactive API docs: http://127.0.0.1:8000/docs

Docker:

```bash
docker build -t ai-support-platform:v0 .
docker run --rm -p 8000:8000 ai-support-platform:v0
```

Configuration (environment variables or `.env`, see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `APP_NAME` | `AI Support Platform` | Title shown in API docs |
| `CLASSIFIER_PATH` | `models/ticket_classifier.joblib` | Model artifact to load at startup |
| `LOG_LEVEL` | `INFO` | Python logging level |

## 12. Testing

```bash
pytest -v
```

16 tests: HTTP contract (`/health`, `/classify`), six invalid-input cases (missing, empty, blank,
too short, too long, wrong type → 422), model file missing (→ 503, `/health` reports it), model
crash (→ 500, generic message), classifier wrapper behaviour on obvious tickets.

## 13. Evaluation

`ml/train.py` holds out 25% of the dataset and reports accuracy, macro F1 and a per-class
precision/recall/F1 table; results are saved to `models/metrics.json`.

Current model (`v0.1.0`, 200 rows, 150 train / 50 test, measured locally):

| Metric | Value |
|---|---|
| Accuracy | 0.90 |
| Macro F1 | 0.90 |
| F1 — account_access / billing / feature_request / technical_issue | 0.81 / 0.92 / 1.00 / 0.87 |

The dataset is small and hand-written; these numbers are optimistic relative to real customer
text and are treated as a baseline, not a claim.

## 14. Architecture Decisions

- [ADR-001 — Classical ML model as the baseline classifier](docs/adr/ADR-001-classical-ml-baseline-classifier.md)
- [ADR-002 — Serve the model inside the API process](docs/adr/ADR-002-in-process-model-serving.md)
- [ADR-003 — Train the model inside the Docker image build](docs/adr/ADR-003-model-trained-in-docker-image.md)

## 15. Performance / Scaling Notes

Measured locally (Windows 11 laptop, single uvicorn process, loopback) with
`scripts/measure_latency.py`:

| Run | Throughput | P50 | P95 | P99 |
|---|---|---|---|---|
| 1000 requests, concurrency 1 | 81.7 req/s | 11.6 ms | 16.0 ms | 21.1 ms |
| 1000 requests, concurrency 10 | 69.9 req/s | 133.8 ms | 211.0 ms | 282.6 ms |

Model inference alone: 1.68 ms per prediction — about 14% of the request time; the rest is the
HTTP stack.

Known limitation: throughput has a ~80 req/s ceiling regardless of client concurrency, because
inference runs serially inside one process (Python's GIL prevents parallel CPU-bound work in the
thread pool). Ten concurrent clients do not get more capacity; they queue, which is why P50
rises ~11×. This is acceptable at current load and with a 2 ms model; it becomes the first thing
to fix when a heavier model or real concurrent traffic arrives (multiple worker processes, then a
separate model service).

Theoretical scaling beyond this machine is discussed per version in `docs/versions/`; none of it
has been measured yet.
