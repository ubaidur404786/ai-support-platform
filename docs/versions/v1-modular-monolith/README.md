# v1 — Modular Monolith

Still one FastAPI process and one Docker image, but the code is now split by feature, and a
new `tickets` module stores what it classifies.

![v1 architecture](../../architecture/v1.svg)

## What this version adds

- `POST /tickets` — classifies the text, stores the ticket, returns 201 with a generated id
- `GET /tickets/{id}` — 200, or 404 if unknown
- `GET /tickets?label=...&needs_review=...` — filtered list with a total
- **Review flag**: predictions below `LOW_CONFIDENCE_THRESHOLD` (0.55) are marked
  `needs_review: true` instead of being silently trusted
- Module structure: `core/`, `health/`, `classification/`, `tickets/`, each with router /
  schemas / service / repository as needed
- Dependency injection: endpoints declare what they need; the "model not loaded" 503 check
  lives in one place
- 43 tests, including business-logic tests that run without HTTP or the real model

## Run

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python ml/train.py
uvicorn app.main:app
```

Interactive docs: http://127.0.0.1:8000/docs — three groups: health, classification, tickets.

## Test

```bash
pytest -v
```

## Try it

```bash
curl -X POST http://127.0.0.1:8000/tickets -H "Content-Type: application/json" -d '{"text": "I was charged twice for my subscription"}'
curl "http://127.0.0.1:8000/tickets?needs_review=true"
```

## See the limitation for yourself

Run two servers and watch them disagree:

```bash
uvicorn app.main:app --port 8000
uvicorn app.main:app --port 8001
```

Post a ticket to 8000, then list tickets on 8001 — it is not there. Each process has its own
in-memory store, so the API cannot be scaled horizontally until storage moves out of it.

## Results (measured locally, same session)

| | Throughput | P50 |
|---|---|---|
| v0 `/classify` | 57.7 req/s | 16.4 ms |
| v1 `/classify` | 55.2 req/s | 16.9 ms |
| v1 `/tickets` | 54.1 req/s | 17.8 ms |

The restructuring costs about 0.5 ms per request — one dependency resolution.

## Documents

- Full version document: [v1-modular-monolith.md](../v1-modular-monolith.md)
- [ADR-004 — Modular monolith structure and dependency injection](../../adr/ADR-004-modular-monolith-structure.md)
- [ADR-005 — Repository pattern with an in-memory implementation](../../adr/ADR-005-repository-pattern-in-memory.md)
- [ADR-006 — Flag low-confidence predictions for human review](../../adr/ADR-006-low-confidence-human-review.md)
- Previous version: [v0 — Baseline](../v0-baseline.md)
