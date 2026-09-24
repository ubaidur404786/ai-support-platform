# v3 — Pagination

List endpoints return a bounded page instead of the whole table. No new technology: the fix is
a bounded interface, not another component.

![v3 architecture](../../architecture/v3.svg)

## What this version adds

- `GET /tickets?limit=50&offset=100` — `limit` and `offset` query parameters
- A **default** page size (50) so a client that asks for nothing does not receive everything
- A **ceiling** (200) enforced twice: FastAPI returns 422 before the handler runs, and the
  service clamps again for callers that never pass through HTTP
- `total`, `limit` and `offset` in the response, so a client always knows which window it got
- `count()` now applies the same filters as `list()`, sharing one `_filtered()` helper so the
  page and the total can never describe different sets of rows

## Run

```bash
docker compose up -d
```

```bash
uvicorn app.main:app
```

```bash
curl "http://127.0.0.1:8000/tickets?limit=3"
curl "http://127.0.0.1:8000/tickets?limit=2&offset=2"
curl "http://127.0.0.1:8000/tickets?limit=500"
```

The last one returns **422**: `"Input should be less than or equal to 200"`.

## Test

```bash
pytest -v
```

70 tests. The one worth reading is `test_pages_cover_every_ticket_exactly_once` — it walks every
page and asserts the ids come back as `[1..7]` with no duplicates and no gaps. That property is
what makes pagination usable; everything else is detail.

## Results (measured locally, 13,000 rows)

| | Rows returned | Client-measured |
|---|---|---|
| v2, unpaged | 13,000 | 2180 ms |
| v3, page of 50 | 50 | ~90 ms |

The server's work is now a function of `limit`, not of table size.

**Two measured findings that shape the next version:**

`OFFSET` walks rows it discards — `OFFSET 12900` made PostgreSQL scan 12,950 index entries to
return 50 rows, 17× the work of the first page. Real, but only 6 ms at this size, which is why
keyset pagination was not built yet.

`total` costs more than the data — the COUNT query took 22.3 ms against 0.36 ms for the page,
roughly 60 to 1. That contradicted the assumption made when designing the feature, and it is
now the strongest candidate for the next change.

## Documents

- Full version document: [v3-pagination.md](../v3-pagination.md)
- [ADR-010 — Offset pagination for list endpoints](../../adr/ADR-010-offset-pagination.md)
- [ADR-011 — Bounded work per request](../../adr/ADR-011-bounded-work-per-request.md)
- Previous version: [v2 — PostgreSQL](../v2-postgresql.md)
