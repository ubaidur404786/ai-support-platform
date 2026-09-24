# AI Support Platform

An AI-powered support and knowledge automation platform, built as one continuously evolving
system. Each version starts from a measured limitation of the previous one and introduces the
smallest architectural change that solves it.

**Current version: v3 — Pagination** (branch `v3-pagination`)

## 1. Project Overview

The platform receives support requests, classifies them, stores them durably, and — in later
versions — searches an organizational knowledge base, generates grounded answers, and performs
controlled actions such as creating or updating tickets.

The engineering goal is a production-style AI system whose every component can be justified:
what problem forced it in, what it costs, and what happens when it fails. The architecture
history is preserved as branches (`v0-baseline`, `v1-modular-monolith`, `v2-postgresql`, `v3-pagination`, ...)
and documented in [`docs/versions/`](docs/versions/) and [`docs/adr/`](docs/adr/).

## 2. Problem Being Solved

Support teams receive a stream of free-text requests that someone has to read and route to the
right team. That work is repetitive, slow, and error-prone. The platform routes each request
automatically, keeps a durable record of what it decided, and makes its uncertainty visible so
a human steps in where the model is unsure rather than everywhere.

## 3. Main AI Capabilities

| Capability | Status | Since |
|---|---|---|
| Ticket classification (billing / technical issue / account access / feature request) | Available | v0 |
| Automatic classification on ticket submission, with the result stored | Available | v1 |
| Low-confidence predictions flagged for human review | Available | v1 |
| Durable, queryable ticket history shared across processes | Available | v2 |
| Paged, bounded ticket listing with filters | Available | v3 |
| Knowledge base search and retrieval-augmented answers | Planned | — |
| Controlled actions (create / update tickets) with human approval | Planned | — |
| Model evaluation, monitoring, and safe rollout | Planned | — |

## 4. Current Architecture

![v3 architecture](docs/architecture/v3.svg)

A stateless FastAPI process organised by feature, with a TF-IDF + Logistic Regression
classifier loaded at startup, and PostgreSQL as the system of record. No cache, queue, or
external service yet.

```
app/
├── main.py          create_app(): lifespan, wiring, router registration
├── core/            settings, logging, database engine and session
├── health/          GET /health  (model + database status)
├── classification/  the model + POST /classify
└── tickets/         POST/GET /tickets  (router → service → repository → PostgreSQL)
```

## 5. Request Flow

`POST /tickets`:

1. Pydantic validates the body: required, 3–5000 characters, not blank. Invalid → 422.
2. FastAPI resolves dependencies: database session → repository → `TicketService`, plus the
   classifier. If the model is not loaded, `get_classifier` raises 503 before any logic runs.
3. `TicketService.submit` classifies the text, compares the confidence against
   `LOW_CONFIDENCE_THRESHOLD`, builds a `Ticket`, and hands it to the repository.
4. The repository inserts the row and commits. PostgreSQL assigns the id from a sequence.
5. Response: 201 with `{id, text, label, confidence, model_version, needs_review, created_at}`.

Failure paths: a database error is rolled back and re-raised as `StorageError`, which the
router turns into **503** — the request was valid and can be retried. Any other unexpected
exception → 500 with a generic message and a logged traceback.

`GET /tickets/{id}` returns 200 or 404. `GET /tickets` accepts `label` and `needs_review`
filters plus `limit` and `offset`, all of which become SQL. An oversized `limit` is rejected
with 422 before the handler runs, and the service clamps it again for callers that do not
arrive over HTTP ([ADR-011](docs/adr/ADR-011-bounded-work-per-request.md)). `POST /classify`
classifies without storing.
`GET /health` reports `model_loaded`, `model_version`, and `database_reachable`, and downgrades
`status` to `degraded` when either is unavailable.

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
     → confidence < threshold ? needs_review = true : false
     → stored in PostgreSQL with the model version that produced it
```

The classifier has no "unknown" class: every input receives one of four labels. The confidence
threshold keeps an uncertain guess from being acted on silently
([ADR-006](docs/adr/ADR-006-low-confidence-human-review.md)). Storing `model_version` alongside
each prediction means a later model change can be evaluated against what the old one decided.

## 7. Data Flow

Tickets are written to the `tickets` table in PostgreSQL and survive process restarts. Any
number of API processes share the same rows. Ids come from `tickets_id_seq`, so concurrent
writers cannot collide. `POST /classify` stores nothing. The schema is versioned by Alembic;
the model file and its metrics are produced at training time.

## 8. Technology Stack

| Layer | Technology | Why (short) |
|---|---|---|
| Language | Python 3.12 | Standard for AI engineering |
| API | FastAPI + Uvicorn | Validation, generated docs, dependency injection, sync endpoints in a thread pool |
| Validation / config | Pydantic, pydantic-settings | Typed request/response models; settings from environment variables |
| Model | scikit-learn (TF-IDF + Logistic Regression), joblib | Kilobyte-sized, ~2 ms CPU inference, standard metrics |
| Storage | PostgreSQL 17, SQLAlchemy 2.0 (sync), psycopg 3 | Durable, shared, queryable; see [ADR-007](docs/adr/ADR-007-postgresql-system-of-record.md) and [ADR-008](docs/adr/ADR-008-sqlalchemy-orm-sync-sessions.md) |
| Schema | Alembic | Versioned migrations; also builds the test database |
| Paging | Offset pagination (`LIMIT`/`OFFSET`) | Bounded responses; see [ADR-010](docs/adr/ADR-010-offset-pagination.md) |
| Tests | pytest, httpx | API tests against a real database, plus business-logic tests with no HTTP and no model |
| Container | Docker, Docker Compose | Reproducible runtime; PostgreSQL with one command |

Dependencies: `requirements.in` lists direct dependencies; `requirements.txt` is the frozen,
pinned set used for installs and the Docker build.

## 9. Current Version

**v3 — Pagination.** See [docs/versions/v3-pagination.md](docs/versions/v3-pagination.md) for
the full problem / solution / trade-off / measurement write-up, and
[docs/versions/v3-pagination/README.md](docs/versions/v3-pagination/README.md) for a short guide.

## 10. Version Evolution

| Version | Branch | Problem it solves | Status |
|---|---|---|---|
| v0 | `v0-baseline` | A support ticket needs to be classified over HTTP with a free, local model | Complete |
| v1 | `v1-modular-monolith` | Tickets must be stored and uncertainty made visible; the flat layout has nowhere to put business logic | Complete |
| v2 | `v2-postgresql` | State lives inside the process: data is lost on restart, cannot be shared between replicas, and blocks running more workers | Complete |
| v3 | `v3-pagination` | `GET /tickets` had no paging: 13,000 rows took 2.18 s and returned everything, letting the caller choose the server's workload | Complete |
| v4 | — | `total` costs 60x the page query it accompanies (22.3 ms vs 0.36 ms) | Next |

Old branches remain on GitHub as engineering history.

## 11. How to Run

Requirements: Python 3.11 or 3.12, and Docker.

Start the database:

```bash
docker compose up -d
```

Then the application:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python ml/train.py               # produces models/ticket_classifier.joblib
alembic upgrade head             # creates the schema
uvicorn app.main:app
```

Interactive API docs: http://127.0.0.1:8000/docs

```bash
curl -X POST http://127.0.0.1:8000/tickets -H "Content-Type: application/json" -d '{"text": "I was charged twice for my subscription"}'
curl "http://127.0.0.1:8000/tickets?needs_review=true&limit=20"
curl "http://127.0.0.1:8000/tickets?limit=50&offset=50"
```

Configuration (environment variables or `.env`, see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `APP_NAME` | `AI Support Platform` | Title shown in API docs |
| `CLASSIFIER_PATH` | `models/ticket_classifier.joblib` | Model artifact to load at startup |
| `LOW_CONFIDENCE_THRESHOLD` | `0.55` | Predictions below this are flagged `needs_review` |
| `DEFAULT_PAGE_SIZE` | `50` | Page size when the client does not specify one |
| `MAX_PAGE_SIZE` | `200` | Hard ceiling; a larger `limit` is rejected with 422 |
| `DATABASE_URL` | `postgresql+psycopg://support:support@localhost:5432/support_platform` | Database connection |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `10` | Connections per process — workers × (pool + overflow) must stay under PostgreSQL's limit |
| `DB_ECHO` | `false` | Print every SQL statement |
| `LOG_LEVEL` | `INFO` | Python logging level |

The Compose credentials are local development values only. Real deployments take them from the
environment or a secret manager.

## 12. Testing

The suite uses a separate database, created once:

```bash
docker compose exec -T db psql -U support -d support_platform -c "CREATE DATABASE support_platform_test;"
```

```bash
pytest -v
```

70 tests:

- **API tests** against a real PostgreSQL database — create / read / list / filter / paginate,
  with tables truncated before each test so ids are predictable. The clearest one walks every
  page and asserts the ids come back with no duplicates and no gaps.
- **Failure cases** — invalid input (six variants), unknown id (404), non-numeric id (422),
  model not loaded (503), storage unavailable (503 on all three ticket endpoints), prediction
  crash (500), database unreachable (health reports `degraded`).
- **Business-logic tests** — `TicketService` with a fake classifier and an in-memory repository
  from `tests/fakes.py`: no HTTP, no server, no database. These passed the PostgreSQL migration
  with one import changed, which is the evidence that storage never leaked into the service.
- **Schema tests by construction** — the test database is built by running the real Alembic
  migrations, so a model change with no matching migration fails the suite.

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
text and are treated as a baseline, not a claim. The model has no out-of-scope class: the input
`"hello"` is classified as `technical_issue` at 0.31 confidence, which is why low-confidence
results are flagged rather than trusted.

## 14. Architecture Decisions

- [ADR-001 — Classical ML model as the baseline classifier](docs/adr/ADR-001-classical-ml-baseline-classifier.md)
- [ADR-002 — Serve the model inside the API process](docs/adr/ADR-002-in-process-model-serving.md)
- [ADR-003 — Train the model inside the Docker image build](docs/adr/ADR-003-model-trained-in-docker-image.md)
- [ADR-004 — Modular monolith structure and dependency injection](docs/adr/ADR-004-modular-monolith-structure.md)
- [ADR-005 — Repository pattern with an in-memory implementation](docs/adr/ADR-005-repository-pattern-in-memory.md) *(superseded by ADR-007)*
- [ADR-006 — Flag low-confidence predictions for human review](docs/adr/ADR-006-low-confidence-human-review.md)
- [ADR-007 — PostgreSQL as the system of record](docs/adr/ADR-007-postgresql-system-of-record.md)
- [ADR-008 — SQLAlchemy ORM with synchronous sessions](docs/adr/ADR-008-sqlalchemy-orm-sync-sessions.md)
- [ADR-009 — Versioned migrations from the first table](docs/adr/ADR-009-alembic-migrations.md)
- [ADR-010 — Offset pagination for list endpoints](docs/adr/ADR-010-offset-pagination.md)
- [ADR-011 — Every request must do a bounded amount of work](docs/adr/ADR-011-bounded-work-per-request.md)

## 15. Performance / Scaling Notes

Measured locally (Windows 11 laptop, single uvicorn process unless stated, PostgreSQL 17 in
Docker, loopback) with `scripts/measure_latency.py`, 1000 requests per run, **all runs in one
session**:

| Version | Endpoint | Concurrency | Throughput | P50 | P95 | P99 |
|---|---|---|---|---|---|---|
| v1 | `/tickets` (in memory) | 1 | 48.6 req/s | 19.4 ms | 31.2 ms | 49.2 ms |
| v2 | `/tickets` (PostgreSQL) | 1 | 27.6 req/s | 33.9 ms | 51.2 ms | 93.3 ms |
| v2 | `/classify` (no database) | 1 | 50.0 req/s | 16.1 ms | 42.4 ms | 66.5 ms |
| v2 | `/tickets` | 10 | 44.5 req/s | 190.7 ms | 420.8 ms | 717.0 ms |

Listing, with 13,000 rows in the table (client-measured, includes client JSON parsing):

| Version | Request | Rows returned | Time |
|---|---|---|---|
| v2 | `GET /tickets` (unpaged) | 13,000 | 2180 ms |
| v3 | `GET /tickets?limit=50` | 50 | ~90 ms |

The server's work is now a function of `limit`, not of table size. In the database the page
query itself executes in 0.36 ms.

Model inference alone is 1.7 ms. A durable write costs about **+14.5 ms at P50** — the price of
durability, stated rather than hidden. The endpoint that writes nothing is unchanged.

**Concurrency began helping in v2.** In v0 and v1, raising concurrency only added queueing and
throughput fell. Here it rises 61% (27.6 → 44.5 req/s) within a single process, because a
thread waiting on the database releases the GIL and another thread can run inference meanwhile.

**Correctness under concurrency:** 3,006 rows written by concurrent requests produced 3,006
distinct ids. Ids come from a database sequence, so no process can collide with another.

**Measurement hygiene.** Identical v0 code measured 81.7 req/s on one day and 57.7 req/s five
days later on the same laptop. Benchmarks here are only compared when taken in the same session
on the same machine; cross-day comparisons are reported as invalid rather than as regressions.

**Known limitations, all measured:**

1. *`total` costs more than the data it accompanies.* The `COUNT` query runs on every list
   request and executes in 22.3 ms against 0.36 ms for the page — roughly 60 to 1. PostgreSQL
   cannot store a row count, because under MVCC the number of visible rows depends on the asking
   transaction. This is the next thing to address.
2. *Deep pages do work they discard.* `OFFSET 12900` made PostgreSQL walk 12,950 index entries
   to return 50 rows — 17x the first page, growing linearly. Only 6 ms at this size and
   invisible through HTTP, which is why keyset pagination has not replaced offset yet.
3. *Multi-worker scaling is unverified on this machine.* Four workers gave no gain over one
   (44.8 vs 44.5 req/s), and PostgreSQL showed only one worker's pool in use during the run —
   consistent with Windows lacking `SO_REUSEPORT`, but not proven. No horizontal-scaling claim
   is made from these numbers.
4. *Serial inference per process.* Carried over from v0. The database's I/O wait masks it
   slightly; a heavier model would expose it immediately.
5. *An open index question.* `needs_review` is not indexed, on the assumption that roughly half
   the rows would match. In the generated data only 4% do, which is selective enough that a
   partial index would likely help. The right answer depends on the real flag rate.

Theoretical scaling beyond this machine is discussed per version in `docs/versions/`; none of
it has been measured.
