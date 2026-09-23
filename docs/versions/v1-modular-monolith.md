# Version v1 — Modular Monolith

Branch: `v1-modular-monolith`
API version: `0.2.0` · Model version: `v0.1.0` (unchanged)
Status: complete

## Problem

The platform's next capabilities are known: a ticket is submitted, classified automatically,
and later answered from a knowledge base or routed to a human. That introduces new concepts
(tickets, documents, answers), each with its own request shapes, logic, and storage.

Two concrete requirements appear immediately:

1. Submitted tickets must be **stored and retrievable**, not classified and forgotten.
2. When the model is **unsure**, the result must be visible as uncertain so a human can check
   the routing instead of the system acting on a guess.

## Current Architecture

v0 was a flat package:

```
app/
├── main.py         one router, both endpoints, model loading, logging setup
├── config.py
├── schemas.py      every request/response shape
└── classifier.py
```

One FastAPI process, model loaded at startup, no storage.

## Why the Old Design Is Not Enough

This is a maintainability problem, not a performance one. With the flat layout:

- `schemas.py` would hold classification, ticket, and document shapes together — unrelated
  concepts in one file that every module has to import.
- `main.py` would hold every endpoint, so any new feature edits the file everything depends on.
- There is **no place for business logic**. "Classify the ticket, then decide whether a human
  must review it" is neither an HTTP concern nor a model concern. In v0 it would have to live
  inside an endpoint function, where it cannot be tested without starting an HTTP client.
- Endpoints reached the model through `request.app.state.classifier` directly, so the
  "is the model loaded?" check had to be repeated in every endpoint that needed it.

Boundaries are cheaper to introduce at four files than at twenty.

## Solution

A **modular monolith**: one process, one deployable, one Docker image — split internally by
feature, with the same layers inside each feature.

![v1 architecture](../architecture/v1.svg)

```
app/
├── main.py                  create_app(): loads the model, creates the repository, registers routers
├── core/                    settings and logging (shared by everything, owns nothing)
├── health/                  GET /health
├── classification/          the model + POST /classify
└── tickets/                 NEW: submit, store, read tickets
```

Layers inside a feature module:

| File | Responsibility | Knows about |
|---|---|---|
| `router.py` | HTTP: status codes, 404, query parameters | HTTP, service |
| `schemas.py` | Request/response shapes (Pydantic) | — |
| `models.py` | Domain object (`Ticket` dataclass) | — |
| `service.py` | Business logic | domain, repository, classifier |
| `repository.py` | Storage | domain only |
| `dependencies.py` | Wiring: how a router obtains a service | all of the above |

New endpoints:

- `POST /tickets` → 201 with `{id, text, label, confidence, model_version, needs_review, created_at}`
- `GET /tickets/{id}` → 200, or 404 if unknown
- `GET /tickets?label=...&needs_review=...` → `{total, items}`

New behaviour: a submitted ticket is classified, stored with a generated id, and marked
`needs_review: true` when the model's confidence is below `LOW_CONFIDENCE_THRESHOLD` (0.55).

## Why This Solution?

**Module-by-feature rather than layer-by-type.** Options considered:

| Option | Verdict |
|---|---|
| Keep flat, add files as needed | Works to roughly ten files, then every file imports every other |
| Top-level `routers/`, `services/`, `schemas/` | Common, but one feature is spread across four folders; extracting or deleting a feature means hunting through all of them |
| **Module-by-feature with layers inside** | Everything about tickets lives in `tickets/`. Extracting it into its own service later is a folder move, not a search |
| Microservices now | Rejected: one developer, no independent scaling requirement, and a network boundary between "tickets" and "classification" would cost latency and operations for nothing |

**Dependency injection instead of reaching into `app.state`.** Endpoints declare what they need
(`service: TicketService = Depends(get_ticket_service)`) and FastAPI supplies it. Two payoffs
appeared immediately: the 503 "model not loaded" check lives in one place
(`get_classifier`) and every endpoint that needs the model inherits it; and the service can be
constructed in a test with a fake classifier, so business logic is tested without HTTP, without
a server, and without the real model.

**Repository pattern.** All storage access goes through one class with `add`, `get`, `list`.
Today it wraps a dictionary. In v2 the same methods run SQL, and the service and router do not
change.

**A confidence threshold rather than rejecting low-confidence predictions.** The classifier has
no "unknown" class — given `"hello"` it must still choose one of four labels, and it returned
`technical_issue` at 0.31 confidence. Discarding such results would lose the ticket; acting on
them silently would mis-route it. Flagging keeps the ticket and makes the uncertainty visible.

See ADRs: [ADR-004](../adr/ADR-004-modular-monolith-structure.md),
[ADR-005](../adr/ADR-005-repository-pattern-in-memory.md),
[ADR-006](../adr/ADR-006-low-confidence-human-review.md).

## New Trade-offs

- **More files and more indirection.** Following one request means reading router →
  dependencies → service → repository. Four files instead of one.
- **Risk of ceremony.** A service layer for a feature with no logic is pure overhead. The rule
  applied here: a module gets a `service.py` only when it has logic that is neither HTTP nor
  storage. `classification/` has none, so it has no service.
- **State is in the process.** Tickets do not survive a restart, and two processes cannot see
  each other's tickets (measured below). Deliberate, documented, and the trigger for v2.
- **No deduplication.** Submitting the same text twice creates two tickets. Acceptable now;
  idempotency becomes a real concern once submissions arrive from a queue.
- **The domain model and the API schema are separate classes** with nearly identical fields.
  That duplication is the price of being able to change either one independently; it pays off
  in v2 when `Ticket` becomes a database model.

## What Changed

Structure (no behaviour change, proven by the existing 16 tests passing unmodified except imports):

- `app/config.py` → `app/core/config.py`; logging setup extracted to `app/core/logging.py`
- `app/classifier.py` → `app/classification/classifier.py`
- `app/schemas.py` split into `app/classification/schemas.py` and `app/health/schemas.py`
- Endpoints moved from `main.py` into per-module `router.py` files with `tags` for the docs page
- The 503 check moved from the endpoint into `classification/dependencies.py:get_classifier`
- `main.py` reduced to `create_app()`: lifespan, wiring, router registration

New:

- `app/tickets/` — `models.py`, `schemas.py`, `repository.py`, `service.py`, `dependencies.py`, `router.py`
- `LOW_CONFIDENCE_THRESHOLD` setting (default 0.55)
- `InMemoryTicketRepository` with a `threading.Lock` around id generation, because FastAPI
  runs synchronous endpoints in a thread pool and two requests can reach it at once
- `tests/test_tickets_api.py` (17 cases) and `tests/test_ticket_service.py` (10 cases)
- `tickets_client` fixture: a fresh app per test, so ticket state does not leak between tests
- `scripts/measure_latency.py` now treats any 2xx as success (`POST /tickets` returns 201)

## How to Test

```bash
pytest -v
```

Manual, with the server running (`uvicorn app.main:app`):

```bash
curl -X POST http://127.0.0.1:8000/tickets -H "Content-Type: application/json" -d '{"text": "I was charged twice for my subscription"}'
curl -X POST http://127.0.0.1:8000/tickets -H "Content-Type: application/json" -d '{"text": "hello"}'
curl http://127.0.0.1:8000/tickets
curl "http://127.0.0.1:8000/tickets?needs_review=true"
curl http://127.0.0.1:8000/tickets/999
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/tickets -ContentType "application/json" -Body '{"text": "I was charged twice for my subscription"}'
Invoke-RestMethod http://127.0.0.1:8000/tickets
```

Latency (server started without `--reload`):

```bash
python scripts/measure_latency.py --requests 1000 --concurrency 1
python scripts/measure_latency.py --url http://127.0.0.1:8000/tickets --requests 1000 --concurrency 1
```

Demonstrate the storage limitation — two servers, two terminals:

```bash
uvicorn app.main:app --port 8000
uvicorn app.main:app --port 8001
```

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/tickets -ContentType "application/json" -Body '{"text": "I need a refund for my payment"}' | Select-Object id, label
Invoke-RestMethod http://127.0.0.1:8001/tickets
```

## Expected Result

- `pytest -v` collects and passes **43** tests.
- A billing ticket returns 201 with `label: billing` and `needs_review: false`.
- `"hello"` returns 201 with a confidence below 0.55 and `needs_review: true`.
- `GET /tickets/999` returns 404; `GET /tickets/abc` returns 422.
- Two servers: the POST to port 8000 returns `id 1`; `GET` on port 8001 returns `total 0`.
- `/docs` shows three groups: health, classification, tickets.

## Measurements

All numbers **measured locally**, Windows 11 laptop, single uvicorn process, loopback.

### Did the restructuring cost performance?

Measured back to back in one session, switching branches between runs:

| Version | Endpoint | Throughput | P50 | P95 | P99 |
|---|---|---|---|---|---|
| v0 (`main`) | `/classify` | 57.7 req/s | 16.4 ms | 23.0 ms | 38.1 ms |
| v1 | `/classify` | 55.2 req/s | 16.9 ms | 26.8 ms | 41.2 ms |
| v1 | `/tickets` | 54.1 req/s | 17.8 ms | 24.3 ms | 37.5 ms |

The refactor costs roughly **0.5 ms at P50** — one extra dependency resolution per request,
within run-to-run noise. `/tickets` adds about 1.4 ms over `/classify`: the same inference plus
a dictionary write and a larger response body.

### Measurement hygiene (a lesson recorded deliberately)

The v0 numbers in the previous version document were 81.7 req/s and P50 11.6 ms. Re-running
**the same v0 code** five days later on the same laptop gave 57.7 req/s and P50 16.4 ms — a 29%
difference caused entirely by machine state (CPU frequency, thermal conditions, background
processes), not by any code change.

Rule adopted for the rest of the project: **benchmark numbers are only comparable when taken in
the same session on the same machine.** Cross-day comparisons are reported as invalid, not as
regressions.

### The storage limitation, demonstrated

Two independent servers on ports 8000 and 8001, same code, same model:

```
POST http://127.0.0.1:8000/tickets   ->  201  { "id": 1, "label": "billing" }
GET  http://127.0.0.1:8001/tickets   ->  200  { "total": 0, "items": [] }
```

Each process has its own `InMemoryTicketRepository`, its own dictionary, and its own id
counter. Two replicas therefore produce duplicate ids, and a read can miss a write that
happened a second earlier.

### Tests

43 tests passing: 16 carried over from v0 (unchanged except import paths), 17 ticket API tests,
10 ticket service tests.

## Interview Explanation

> "v0 was a flat package with one router. Before adding features I split it into a modular
> monolith — still one process and one image, but organised by feature, with router, service
> and repository layers inside each module. The trigger was maintainability, not performance:
> the next feature needed business logic that belonged in neither the endpoint nor the model,
> and in the flat layout it would have had to live inside an endpoint function where it can
> only be tested through HTTP. I also moved from reaching into application state to FastAPI's
> dependency injection, which let me put the 'model not loaded' 503 check in one place and test
> the ticket service with a fake classifier — no HTTP, no server, no real model.
>
> I verified the refactor was behaviour-preserving by running the existing test suite unchanged,
> and measured both versions back to back in one session: about 0.5 ms P50 difference, which is
> one dependency resolution. That back-to-back detail matters — my earlier v0 numbers looked 29%
> better, but re-running the identical code days later reproduced the slower figures, so the
> apparent regression was the laptop, not the code.
>
> Storage is deliberately a dictionary in memory, and I demonstrated why that has to change: I
> ran two servers, posted a ticket to one, and read an empty list from the other. That means the
> obvious fix for the throughput ceiling — running more worker processes — is blocked by the
> storage design, not by the framework. That is what justifies a shared database next."

## Next Possible Limitation

1. **State in the process — the immediate trigger.** Demonstrated above. Blocks horizontal
   scaling, loses data on restart, and gives duplicate ids across replicas. Next version:
   PostgreSQL behind the same repository interface.
2. **No query capability worth the name.** `list()` loads every ticket into a Python list and
   filters it. Fine at three tickets, hopeless at a hundred thousand. Needs indexes, paging,
   and filtering in the database.
3. **No deduplication or idempotency.** Submitting identical text twice creates two tickets.
   Matters once submissions arrive from a retrying client or a queue.
4. **The model has no "unknown" class.** `"hello"` was classified as `technical_issue` at 0.31.
   The review flag contains the damage but does not fix it. A better model, or an explicit
   out-of-scope class, is a separate AI problem from the architecture work.
5. **Still serial inference.** Carried over from v0 and unchanged: about 55 req/s, and
   concurrency queues rather than scales.
