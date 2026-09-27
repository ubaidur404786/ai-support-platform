# AI Support Platform

An AI-powered support and knowledge automation platform, built as one continuously evolving
system. Each version starts from a measured limitation of the previous one and introduces the
smallest architectural change that solves it.

**Current version: v6 — Document ingestion and keyword search** (branch `v6-document-ingestion`)

## 1. Project Overview

The platform receives support requests, classifies them, stores them durably, and — in later
versions — searches an organizational knowledge base, generates grounded answers, and performs
controlled actions such as creating or updating tickets.

The engineering goal is a production-style AI system whose every component can be justified:
what problem forced it in, what it costs, and what happens when it fails. The architecture
history is preserved as branches (`v0-baseline`, `v1-modular-monolith`, `v2-postgresql`,
`v3-pagination`, `v4-authentication`, `v5-rate-limiting`, `v6-document-ingestion`, ...)
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
| Per-organisation isolation of every ticket, enforced in SQL | Available | v4 |
| Per-caller limits on model inference and login attempts (429 + `Retry-After`) | Available | v5 |
| Knowledge-base documents (.txt / .md / .pdf) extracted, chunked and stored per organisation | Available | v6 |
| Keyword search over document chunks, with a measured retrieval baseline | Available | v6 |
| Meaning-based (embedding) search and retrieval-augmented answers | Planned | — |
| Controlled actions (create / update tickets) with human approval | Planned | — |
| Model evaluation, monitoring, and safe rollout | Planned | — |

## 4. Current Architecture

![v6 architecture](docs/architecture/v6.svg)

A stateless FastAPI process organised by feature, with a TF-IDF + Logistic Regression
classifier loaded at startup, and PostgreSQL as the system of record. Callers authenticate with
a bearer token, and every ticket belongs to exactly one organisation. Every expensive endpoint
has a per-caller budget — a token bucket held in process memory, checked before the expensive
work runs. Knowledge-base documents are split into overlapping chunks and stored in PostgreSQL
with a full-text index. No cache, queue, or external service yet.

```
app/
├── main.py          create_app(): lifespan, wiring, router registration
├── core/            settings, logging, database engine/session, shared errors, rate_limit.py,
│                    request_limits.py (body-size middleware)
├── health/          GET /health  (model + database status)
├── auth/            POST /auth/register, POST /auth/login, get_current_user, the limit dependencies
├── classification/  the model + POST /classify   (requires a token since v5)
├── tickets/         POST/GET /tickets  (router → service → repository → PostgreSQL)
└── documents/       POST/GET/DELETE /documents, GET /documents/search
                     (extraction.py → chunking.py → service → repository → PostgreSQL)
```

## 5. Request Flow

`POST /tickets`:

1. `get_current_user` reads the `Authorization: Bearer` header, verifies the token's signature
   and expiry, loads the `users` row, and checks `is_active`. No header → **401**; anything wrong
   with the token → **401** with the same message. Being a dependency, this runs *before* the
   handler body, so an anonymous caller never reaches endpoint code.
2. `limit_inference` takes one token from the caller's inference bucket, keyed on the user id and
   shared with `POST /classify`. Empty → **429** with `Retry-After`, before the model runs.
3. Pydantic validates the body: required, 3–5000 characters, not blank. Invalid → 422.
4. FastAPI resolves dependencies: database session → repository → `TicketService`, plus the
   classifier. If the model is not loaded, `get_classifier` raises 503 before any logic runs.
5. `TicketService.submit` receives `current_user.organization_id` — from the token, never from
   the request body — classifies the text, compares the confidence against
   `LOW_CONFIDENCE_THRESHOLD`, builds a `Ticket`, and hands it to the repository.
6. The repository inserts the row and commits. PostgreSQL assigns the id from a sequence.
7. Response: 201 with `{id, text, label, confidence, model_version, needs_review, created_at}`.

Failure paths: a database error is rolled back and re-raised as `StorageError`, which the
router turns into **503** — the request was valid and can be retried. Any other unexpected
exception → 500 with a generic message and a logged traceback.

`GET /tickets/{id}` returns 200 or 404 — including for a ticket that exists in *another*
organisation, because across a tenant boundary existence is itself information
([ADR-015](docs/adr/ADR-015-cross-tenant-404.md)). `GET /tickets` accepts `label` and
`needs_review` filters plus `limit` and `offset`, all of which become SQL, always alongside a
`WHERE organization_id = …` that is not optional. An oversized `limit` is rejected
with 422 before the handler runs, and the service clamps it again for callers that do not
arrive over HTTP ([ADR-011](docs/adr/ADR-011-bounded-work-per-request.md)). `POST /classify`
classifies without storing; it needs a token and spends the same inference budget as
`POST /tickets`. `POST /auth/login` and `/auth/register` spend an "auth" budget keyed on the
client address — there is no user yet — so a refused attempt never reaches bcrypt
([ADR-016](docs/adr/ADR-016-in-process-token-bucket.md), [ADR-017](docs/adr/ADR-017-rate-limit-keys.md)).
`POST /documents` (multipart upload):

1. `core/request_limits.py` refuses a declared `Content-Length` over 5 MB + 64 KB with **413**
   before anything else runs. This had to be a middleware: FastAPI parses a multipart body before
   any dependency, authentication included ([ADR-020](docs/adr/ADR-020-body-size-limit-before-auth.md)).
2. `get_current_user`, then `limit_ingestion` (20 uploads a minute per user) → 401 / 429.
3. The router reads at most 5 MB + 1 byte → 413 if over.
4. `DocumentService.ingest` hashes the bytes (SHA-256). A file already stored in this organisation
   → **409**, before any extraction.
5. `extraction.py` detects the type from the name **and** the first bytes (415 if unsupported),
   extracts text (pypdf for PDFs; 413 over 300 pages; 422 if unreadable or empty), and removes NUL
   characters PostgreSQL cannot store.
6. `chunking.py` splits the text into ≤ 800-character chunks, breaking at a paragraph, then a
   sentence, then a word, with 100 characters of overlap.
7. The repository writes the document and all its chunks in **one transaction**. PostgreSQL
   computes each chunk's `tsvector` and indexes it.
8. Response: 201 with metadata — never the text itself.

`GET /documents/search?q=…` keeps only letters and digits from the query (so `to_tsquery` syntax
can never pass through), then runs one SQL query: `WHERE organization_id = … AND search_vector @@
'w1 | w2 | …' ORDER BY ts_rank(…) DESC LIMIT n` (n ≤ 20). Another organisation's documents are
404 on read and delete, and absent from search.

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
writers cannot collide. `POST /classify` stores nothing.

Every ticket carries a `NOT NULL organization_id` referencing `organizations`, and every read is
filtered by it. `users` belong to one organisation and store only a bcrypt hash, never a
password. The 13,000 tickets written before v4 were backfilled into a synthetic `default`
organisation by the migration — intact, but owned by a tenant nobody logs into.

Documents are stored as one `documents` row (metadata, the full extracted text, a SHA-256 of
the upload) plus N `document_chunks` rows (text, offsets, a generated `tsvector` with a GIN index).
Chunks carry their own `organization_id` so the tenant filter never depends on a JOIN, and are
removed by `ON DELETE CASCADE` with their document. The original file bytes are **not** kept
([ADR-019](docs/adr/ADR-019-store-extracted-text-not-originals.md)).

The schema is versioned by Alembic;
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
| Authentication | `bcrypt`, `PyJWT`, `email-validator` | Signed bearer tokens and slow salted hashing; see [ADR-012](docs/adr/ADR-012-bearer-tokens.md) and [ADR-014](docs/adr/ADR-014-bcrypt-password-storage.md) |
| Rate limiting | Token bucket in `app/core/rate_limit.py` — no library, no Redis | One process needs no shared store; 2 µs per check; see [ADR-016](docs/adr/ADR-016-in-process-token-bucket.md) |
| Documents | `python-multipart`, `pypdf` | File uploads; pure-Python PDF text extraction with no system libraries |
| Search | PostgreSQL full-text search (`tsvector`, GIN, `ts_rank`) | Stemming and ranking in the database we already run; the baseline embeddings must beat — see [ADR-018](docs/adr/ADR-018-postgres-full-text-search-baseline.md) |
| Tests | pytest, httpx | API tests against a real database, plus business-logic tests with no HTTP and no model |
| Container | Docker, Docker Compose | Reproducible runtime; PostgreSQL with one command |

Dependencies: `requirements.in` lists direct dependencies; `requirements.txt` is the frozen,
pinned set used for installs and the Docker build.

## 9. Current Version

**v6 — Document ingestion and keyword search.** See
[docs/versions/v6-document-ingestion.md](docs/versions/v6-document-ingestion.md) for the full
problem / solution / trade-off / measurement write-up, and
[docs/versions/v6-document-ingestion/README.md](docs/versions/v6-document-ingestion/README.md) for
a short guide.

## 10. Version Evolution

| Version | Branch | Problem it solves | Status |
|---|---|---|---|
| v0 | `v0-baseline` | A support ticket needs to be classified over HTTP with a free, local model | Complete |
| v1 | `v1-modular-monolith` | Tickets must be stored and uncertainty made visible; the flat layout has nowhere to put business logic | Complete |
| v2 | `v2-postgresql` | State lives inside the process: data is lost on restart, cannot be shared between replicas, and blocks running more workers | Complete |
| v3 | `v3-pagination` | `GET /tickets` had no paging: 13,000 rows took 2.18 s and returned everything, letting the caller choose the server's workload | Complete |
| v4 | `v4-authentication` | Every request was anonymous: no row recorded who created it, and any client that could reach the port could read and write every ticket | Complete |
| v5 | `v5-rate-limiting` | One caller could spend unbounded CPU: `/auth/login` is ~0.7–0.9 s of bcrypt per anonymous attempt, and `/classify` was public. Pagination bounded work per request, not requests per caller | Complete |
| v6 | `v6-document-ingestion` | A knowledge platform with no knowledge: nowhere to store a document, nothing to read a file, nothing to find a passage | Complete |
| v7 / v8 | — | Two measured candidates: ingestion blocks the request (300-page PDF 5.8 s) → background processing; keyword search misses paraphrases (hit@3 0.47) → embeddings | Next |

Only v3 diverged from the original roadmap, which had scheduled authentication there.
Measurement inserted pagination first, because the unbounded list had a measured trigger and
authentication did not. Old branches remain on GitHub as engineering history.

The whole chain in one picture — the problem that forced each version, what changed, and what was
measured afterwards:

![Architecture evolution, v0 to v6](docs/architecture/evolution.svg)

Carried forward, measured but not yet fixed: `total` still costs more than the page it
accompanies (5.78 ms vs 0.128 ms); deep offsets degrade linearly; there is no type checker;
rate-limit buckets live in one process, so every extra worker multiplies the limits; document
ingestion runs inside the request; keyword search misses paraphrased questions.

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
curl -X POST http://127.0.0.1:8000/auth/register -H "Content-Type: application/json" -d '{"organization_name": "Acme", "email": "maya@acme.com", "password": "correct-horse-battery"}'
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login -H "Content-Type: application/json" -d '{"email": "maya@acme.com", "password": "correct-horse-battery"}' | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
curl -X POST http://127.0.0.1:8000/tickets -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"text": "I was charged twice for my subscription"}'
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/tickets?needs_review=true&limit=20"
curl -X POST http://127.0.0.1:8000/classify -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"text": "I cannot log in"}'
curl -X POST http://127.0.0.1:8000/documents -H "Authorization: Bearer $TOKEN" -F "file=@evaluation/knowledge_base/refund-policy.md"
curl -G http://127.0.0.1:8000/documents/search -H "Authorization: Bearer $TOKEN" --data-urlencode "q=how long does a refund take"
curl -i http://127.0.0.1:8000/tickets     # 401: no token
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
| `JWT_SECRET_KEY` | **none — required** | Token signing key. The app refuses to start without it; generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `JWT_ALGORITHM` | `HS256` | Signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token lifetime |
| `RATE_LIMIT_ENABLED` | `true` | `false` switches every limit off — for measuring the "before" numbers only |
| `AUTH_RATE_LIMIT_PER_MINUTE` | `10` | Login + registration attempts per client address |
| `INFERENCE_RATE_LIMIT_PER_MINUTE` | `60` | `/classify` + `POST /tickets` per user, one shared budget |
| `INGESTION_RATE_LIMIT_PER_MINUTE` | `20` | Document uploads per user |
| `MAX_DOCUMENT_BYTES` / `MAX_DOCUMENT_PAGES` | `5000000` / `300` | Upload ceilings; larger → 413 |
| `CHUNK_MAX_CHARS` / `CHUNK_OVERLAP_CHARS` | `800` / `100` | Chunk size and overlap |
| `MAX_SEARCH_RESULTS` | `20` | Ceiling on `limit` for `/documents/search` |
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

180 tests, about 187 seconds — most of it bcrypt doing its job (see §15):

- **Document pipeline, no HTTP and no database** (29 tests) — chunks never exceed the maximum;
  offsets point at the exact text; **no character of the document is lost**; neighbours overlap;
  chunks end on sentences; an unbroken 2,000-character word terminates. Extraction: UTF-8 with and
  without a byte-order mark, PDFs page by page (built in memory by `tests/pdf_factory.py`), a `.pdf`
  name without the PDF signature refused, corrupt and text-less PDFs refused, the page limit applied
  before extraction, NUL characters removed.
- **Documents API** (31 tests) — upload, title derivation, duplicates (409) per organisation,
  six kinds of unusable file with nothing stored, size limits, cross-tenant 404 on read, delete and
  search, stemming, `any` vs `all`, operator characters in the query made harmless, bounded results,
  storage failure → 503 on all five endpoints, **a failed chunk insert leaves no document behind**
  (and is reported as 503, not mistaken for a duplicate), an oversized body refused before
  authentication, and a per-user upload budget that reads do not spend.

- **Rate limiting** — the token bucket tested with a fake clock (time moved by assignment, so the
  tests are exact and instant): burst then refuse, exact `Retry-After`, refill, no saving up beyond
  capacity, separate callers, bounded memory, and 50 threads racing for 10 tokens getting exactly
  10. Through HTTP: login refused with 429 and `Retry-After`; **a refused login never calls
  `authenticate`** (the limiter runs before bcrypt, proved rather than assumed); wrong passwords
  and registrations spend the same budget; `/classify` and `POST /tickets` share one inference
  budget and a refused ticket stores nothing; each user has their own budget; anonymous callers get
  401, not 429.

- **Authentication tests** — register, login, duplicate email (409), wrong password and unknown
  email (401, asserted to be *identical*), six invalid-input variants (422), and a test that the
  raw response text of register and login contains neither `"password"` nor `"$2b$"`.
- **Tenant isolation** — one organisation writes a ticket; another gets 404 by id and `total: 0`
  from the list, while the owner can still read it. Proved at the service layer too, so an HTTP
  test cannot pass while the service ignores its argument.
- **Every `/tickets` route parametrised against an anonymous client**, because the risk is not
  "auth is broken", it is "one endpoint was added without it".
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

### Retrieval (v6)

`scripts/evaluate_retrieval.py` uploads 20 help articles (`evaluation/knowledge_base/`) through the
API and asks 30 questions (`evaluation/retrieval_questions.jsonl`), each with one correct article.
Fifteen reuse the article's words ("lexical"); fifteen say the same thing differently
("paraphrase"). Results are saved in `evaluation/results/`.

Keyword search, `match=any`, `ts_rank`, top 5 documents:

| | n | hit@1 | hit@3 | MRR | returned nothing |
|---|---|---|---|---|---|
| overall | 30 | 0.60 | **0.73** | 0.66 | 10% |
| lexical | 15 | 1.00 | 1.00 | 1.00 | 0% |
| paraphrase | 15 | 0.20 | **0.47** | 0.31 | 20% |

Requiring every word (`match=all`) drops lexical hit@3 to 0.80 and paraphrase to **0.00**. Every
paraphrase miss is a vocabulary mismatch: "money back" vs "refund", "admin" vs "Administrators".
The articles and questions were written by the same author, so the lexical 1.00 is an upper bound.
With n=30, one question is 3.3 points. This is a baseline for comparing the next retrieval
method on identical questions, not a claim about real-world accuracy.

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
- [ADR-012 — Signed bearer tokens carrying only the user id](docs/adr/ADR-012-bearer-tokens.md)
- [ADR-013 — The organisation is the tenancy boundary, and its filter is a required argument](docs/adr/ADR-013-organisation-scoped-authorization.md)
- [ADR-014 — bcrypt, used directly, for password storage](docs/adr/ADR-014-bcrypt-password-storage.md)
- [ADR-015 — A read across a tenant boundary answers 404, not 403](docs/adr/ADR-015-cross-tenant-404.md)
- [ADR-016 — In-process token bucket for rate limiting](docs/adr/ADR-016-in-process-token-bucket.md)
- [ADR-017 — Rate-limit keys: client address for auth, user id for inference](docs/adr/ADR-017-rate-limit-keys.md)
- [ADR-018 — Chunks in PostgreSQL, with full-text search as the retrieval baseline](docs/adr/ADR-018-postgres-full-text-search-baseline.md)
- [ADR-019 — Store the extracted text, not the original file](docs/adr/ADR-019-store-extracted-text-not-originals.md)
- [ADR-020 — Refuse oversized request bodies before authentication](docs/adr/ADR-020-body-size-limit-before-auth.md)

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

Authentication, measured in its own session (`DB_ECHO=false`, no `--reload`, Python `httpx`
client):

| Version | Request | n | P50 | Notes |
|---|---|---|---|---|
| v4 | `POST /auth/login` | 10 | **682 ms** | min 655, max 770 |
| v4 | `GET /tickets` (authenticated) | 20 | 33.8 ms | min 29.4, max 41.2 |

`bcrypt.checkpw` measured alone at cost factor 12 takes **682.2 ms** — the same number as the
whole login endpoint. The email lookup and the token signing disappear into rounding: **the
endpoint is the hash.** Inside a `GET /tickets` request the database accounts for 0.429 ms of
33.8 ms (1.3%): 0.183 ms for the `users` lookup that `get_current_user` adds, and 0.246 ms for
the page itself. The rest is Python, ASGI, Pydantic serialisation and loopback — **the database
is not the bottleneck in this system and never has been.**

The v4 numbers are deliberately *not* compared against v3's, because `DB_ECHO` and `--reload`
both changed between the two sessions. An earlier attempt to time login with PowerShell's
`Invoke-RestMethod` reported 2174 ms for a 682 ms request; the client was adding ~1500 ms of its
own. **An instrument has to be cheaper than the thing it measures.**

Query plans, each statement run twice with the second reported, since the first paid for cold
buffers (2.529 ms, and a 2.043 ms planning time):

| Query | Plan | Execution |
|---|---|---|
| `ORDER BY id LIMIT 50` | `Index Scan using tickets_pkey` | 0.145 ms |
| `WHERE organization_id = 1 ORDER BY id LIMIT 50` | `Index Scan using tickets_pkey` + row filter | **0.128 ms** |
| `count(*) WHERE organization_id = 1` | `Index Only Scan using ix_tickets_organization_id`, `Heap Fetches: 0` | 5.78 ms |

**The tenant filter is free.** The difference is jitter — the filtered query is marginally
*lower*, which is how you know it is noise. Reading the cold first run instead would have
"proved" that adding a `WHERE` clause made the query 10x faster.

**The planner declined the new index for the page query**, using `tickets_pkey` with a row filter
instead. Correct: every row is currently in organisation 1, so the index selects 100% of the
table and helps nothing, while `tickets_pkey` supplies the `ORDER BY id` ordering for free. An
index is a possibility the planner may decline, not an instruction.

**An unplanned side effect:** the same index turned `count(*)` into an index-only scan that never
touches the table. v3 recorded this COUNT at 22.3 ms and made it the headline next problem; it is
now 5.78 ms. No speedup is claimed — that was a different session — but the plan is structurally
better, for a reason explainable after the fact and not predicted before it. An index added for
authorization incidentally improved the counter.

**bcrypt's cost is visible in the test suite**, which went from ~7 s (70 tests) to 102.92 s
(99 tests). Not overhead — arithmetic: each `tickets_client` fixture registers (one hash) and
logs in (one verify), ~1.36 s, across roughly 45 tests. `45 × 1.36 ≈ 61 s`, about 60% of the
runtime. A hash fast enough to be free in tests is a hash fast enough to brute-force in
production.

**v5 — rate limits**, measured in one session with a Python `httpx` client, one client address,
`DB_ECHO=false`, no `--reload`:

| Scenario | Limits off | Limits on |
|---|---|---|
| 30 wrong logins, sequential | 29.06 s — 30 × 401, P50 891 ms | **10.54 s** — 11 × 401 (P50 893 ms), 19 × 429 (**P50 6.2 ms**) |
| 100 `/classify`, one user, after 5 warm-up | 100 × 200, P50 24.8 ms | 57 × 200 (P50 21.7 ms), 43 × 429 (**P50 14.8 ms**) |
| limiter `acquire()` alone | — | **2.07 µs** (3.12 µs with 100,000 keys; 14.2 MB for 100,000 buckets) |

A refused login is ~140× cheaper than an allowed one because the address-keyed check needs no
database; a refused `/classify` is only ~1.5× cheaper, because keying on the *user* means the
token check and `users` lookup run first. That is the price of a key that cannot be escaped by
changing network ([ADR-017](docs/adr/ADR-017-rate-limit-keys.md)).

**The limit is per process, measured:** limit 5/min, 40 wrong logins from 8 threads — one worker
allowed exactly **5**; `--workers 2` allowed **8**. Each worker holds its own buckets. This is the
recorded trigger for moving buckets into Redis ([ADR-016](docs/adr/ADR-016-in-process-token-bucket.md)).

The same experiment re-taught standing rule 2: creating a new `httpx` client per request made 40
requests take 47.30 s; over one shared client they took 1.46 s.

**v6 — documents**, one session, `DB_ECHO=false`, no `--reload`, httpx client:

| Upload (`POST /documents`, P50) | Chunks | Time |
|---|---|---|
| text 10 KB / 1 MB / 4.9 MB | 17 / 1,737 / 8,518 | 165 ms / 2,372 ms / **8,406 ms** |
| PDF 10 / 100 / 300 pages | 43 / 416 / 1,251 | 395 ms / 3,594 ms / **5,774 ms** |

For text, most of the time is inserting chunk rows: 1,740 rows took ~1.0–1.2 s in 2 batched SQL
statements, and the GIN index added no measurable cost. For PDFs, extraction dominates (2.5 s at
100 pages). **All of it happens inside the request**, and that is the trigger for background
processing.

Search, `limit=5`: ~23–26 ms on a 20-chunk organisation. On a synthetic 39,588-chunk organisation
where ~95% of chunks match any common word (a worst case), **`ts_rank_cd` took 5,909 ms and
`ts_rank` 108 ms**, with no loss in evaluation quality. Ranking runs for every *matching* chunk
before `LIMIT`, so per-row cost multiplies. Plans: a word in no chunk uses the GIN index (0.356 ms);
a word in 95% of chunks gets a sequential scan (84.6 ms), because the planner declines an index
that selects almost everything.

An anonymous 200 MB upload was answered 401 only after being received in full (4,146 ms). With the
body-size middleware, 413 comes from the headers alone in **1.4–3.2 ms**.

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

**Known limitations:**

1. *Rate limits are per process.* Measured: with two workers a limit of 5 let 8 attempts through.
   Any second worker or replica multiplies every limit. Trigger for a shared store (Redis).
2. *Per-address limits do not stop a distributed attack on login.* 1,000 addresses × 10 attempts a
   minute is still 10,000 bcrypt calls. Needs a global cap on concurrent bcrypt work; a per-email
   limit would help against targeted guessing but lets an attacker lock a victim out.
3. *No token revocation.* A signed token cannot be un-signed. Disabling a user works immediately,
   because the user row is loaded per request, but a token stolen from an active account works
   until it expires — up to 60 minutes.
4. *`total` still costs more than the data it accompanies*: 5.78 ms against 0.128 ms for the page.
   PostgreSQL cannot store a row count, because under MVCC the number of visible rows depends on
   the asking transaction. Improved by accident in v4, not solved.
5. *Deep pages do work they discard.* `OFFSET 12900` made PostgreSQL walk 12,950 index entries to
   return 50 rows — 17x the first page, growing linearly. Only 6 ms at this size and invisible
   through HTTP, which is why keyset pagination has not replaced offset yet.
6. *Still no type checker.* A `Protocol` has no runtime enforcement, and the inline
   `BrokenRepository` double broke in **both** v3 and v4 for the same structural reason: N
   implementations means N manual edits with no compiler help. mypy or Pyright would have caught
   all of them.
7. *The test suite takes 187 seconds* (180 tests; 103 s in v4), which is approaching the point where it stops being run
   often enough to be useful. Both standard fixes cost something real: a lower bcrypt cost factor
   in tests means no longer testing the production configuration, and session-scoping the
   registration means tests share a user.
8. *Multi-worker throughput is unverified on this machine.* Four workers gave no gain over one
   (44.8 vs 44.5 req/s) in v2. v5 did show that two workers **both serve requests** on Windows —
   a limit of 5 letting 8 through is only possible if two processes answered — but no throughput
   claim is made from that.
9. *Serial inference per process.* Carried over from v0. The database's I/O wait masks it
   slightly; a heavier model would expose it immediately.
10. *An open index question.* `needs_review` is not indexed, on the assumption that roughly half
    the rows would match. In the generated data only 4% do, which is selective enough that a
    partial index would likely help. The comment in `app/tickets/models.py` still states the
    wrong assumption.
11. *No per-user visibility and no audit trail.* Every member of an organisation sees everything
    it owns, and `needs_review` records that something needs looking at but not who looked.
12. *No password reset, logout, refresh or rotation.* All real, all deferred.
13. *Reads are not rate limited.* `GET /tickets` is bounded per request (v3) but not per caller.
    No measurement asks for it yet.
14. *Behind a reverse proxy, every caller would share the proxy's address* and therefore one auth
    budget. `X-Forwarded-For` is deliberately ignored until a proxy we control sets it.
15. *Document ingestion blocks the request.* A 300-page PDF holds a worker for 5.8 s, a 4.9 MB text
    file for 8.4 s. A few simultaneous uploads would occupy the thread pool. Trigger for
    background processing.
16. *Keyword search misses meaning.* Paraphrase hit@3 is 0.47, and 20% of paraphrases return
    nothing. Trigger for embeddings.
17. *Search cost grows with the number of matching chunks*, not with `limit` — v3's problem, inside
    the database. Bounded (108 ms at 39,588 chunks, worst case), not flat.
18. *Original files are not kept* — no re-extraction, no download
    ([ADR-019](docs/adr/ADR-019-store-extracted-text-not-originals.md)). *Scanned PDFs are refused*
    (no OCR). *Uploads without `Content-Length` bypass the body-size middleware.*

Theoretical scaling beyond this machine is discussed per version in `docs/versions/`; none of
it has been measured.
