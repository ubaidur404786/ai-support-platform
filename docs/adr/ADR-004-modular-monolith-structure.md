# ADR-004 — Modular monolith: organise by feature, wire with dependency injection

Status: accepted (v1)

## Context

v0 was a flat package: `main.py`, `config.py`, `schemas.py`, `classifier.py`, with both
endpoints and the model loading in one file. The next features (tickets, document ingestion,
question answering) each bring their own request shapes, logic, and storage.

A concrete gap appeared with the first of them: "classify the ticket, then decide whether a
human must review it" is neither an HTTP concern nor a model concern, and the flat layout had
nowhere to put it except inside an endpoint function — where it can only be tested by starting
an HTTP client.

Endpoints also reached into `request.app.state.classifier` directly, so the "is the model
loaded?" check had to be repeated in every endpoint that used the model.

## Options

1. **Stay flat, add files as needed.** No work today. Around ten files, `schemas.py` and
   `main.py` become shared bottlenecks that every change touches.
2. **Layer-by-type at the top level** (`routers/`, `services/`, `schemas/`, `repositories/`).
   Familiar and widely used. One feature is spread across four folders, so understanding,
   extracting, or deleting a feature means visiting all of them.
3. **Module-by-feature, layered inside** (`tickets/router.py`, `tickets/service.py`, ...).
   Everything about a feature is in one folder.
4. **Microservices now.** Separate deployables for tickets and classification.

## Decision

Option 3, plus FastAPI's `Depends` for wiring.

```
app/
├── main.py          create_app(): lifespan, wiring, router registration
├── core/            settings, logging (shared, owns no feature)
├── health/          GET /health
├── classification/  model + POST /classify
└── tickets/         POST/GET /tickets
```

Inside a feature: `router.py` (HTTP only), `schemas.py` (API shapes), `models.py` (domain
object), `service.py` (business logic), `repository.py` (storage), `dependencies.py` (wiring).

A module gets a `service.py` only when it has logic that is neither HTTP nor storage.
`classification/` has none, so it has none.

## Why?

- One folder per feature means a later extraction into a separate service is a folder move
  rather than a search across the codebase. That matters because the roadmap explicitly
  contemplates extracting model serving later.
- Dependency injection put the 503 "model is not loaded" check in exactly one function
  (`get_classifier`), which every endpoint needing the model now inherits.
- It made business logic testable in isolation: `TicketService` is constructed in tests with a
  fake classifier and an empty repository — no HTTP, no server, no model file. Ten of the 43
  tests run that way, and they are the fastest and most precise in the suite.
- It costs nothing operationally: same process, same image, same deployment.

Microservices were rejected because there is one developer, no requirement to scale tickets
separately from classification, and a network boundary between them would add latency and
operational work in exchange for nothing at this stage.

## Trade-offs

Gain: clear ownership of code, isolated business-logic tests, one place for cross-cutting
checks, and seams that match where the system is likely to split later.

Lose:

- More files and more indirection: following one request means reading router → dependencies →
  service → repository.
- A real risk of ceremony — layers that exist because the pattern says so rather than because
  there is logic to hold. Mitigated by the "no service without logic" rule above.
- Measured cost of the extra dependency resolution: about **0.5 ms at P50**
  (55.2 vs 57.7 req/s, measured back to back in one session). Negligible, but not zero.

## Future Trigger

Revisit when:

- A module needs to scale or be deployed independently of the rest (most likely model serving,
  planned as v9) — the module boundary is where that cut is made.
- A module's `service.py` grows beyond what one file can explain, suggesting the feature is
  really two features.
- Cross-module imports start going in both directions, which would mean the boundaries no
  longer match reality.
