# v2 — PostgreSQL

Tickets move out of the API process and into a shared, durable database. The API becomes
stateless; PostgreSQL becomes the system of record.

![v2 architecture](../../architecture/v2.svg)

## What this version adds

- **PostgreSQL 17** via Docker Compose, with a named volume and a health check
- **SQLAlchemy 2.0** (typed style, synchronous) mapping `Ticket` to the `tickets` table
- **Alembic** migrations — the schema is versioned, and the test database is built by running
  the real migrations
- **`StorageError`** — database failures are translated once, so the router never imports
  SQLAlchemy, and return **503** rather than 500
- **`/health` reports `database_reachable`** and downgrades `status` to `degraded`
- Ids come from a database sequence, so concurrent processes cannot collide

`TicketService` and the router did not change: they were written against the repository
interface in v1. The service's only edit was a type hint.

## Run

```bash
docker compose up -d
```

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python ml/train.py
alembic upgrade head
uvicorn app.main:app
```

Interactive docs: http://127.0.0.1:8000/docs

## Test

The test suite uses a separate database, created once:

```bash
docker compose exec -T db psql -U support -d support_platform -c "CREATE DATABASE support_platform_test;"
```

```bash
pytest -v
```

47 tests. Each one truncates the tables first, so ids are predictable.

## See what changed

Durability — post a ticket, stop the server with Ctrl+C, start it again, then list:

```bash
curl http://127.0.0.1:8000/tickets
```

The ticket is still there. In v1 it would not have been.

Failure behaviour — stop the database and watch the API degrade instead of crash:

```bash
docker compose stop db
```

```bash
curl http://127.0.0.1:8000/health
```

`status: degraded`, `database_reachable: false`, still HTTP 200. `POST /tickets` returns 503
`"Storage is unavailable"`.

```bash
docker compose start db
```

## Results (measured locally, same session)

| | Throughput | P50 |
|---|---|---|
| v1 `/tickets` (in memory) | 48.6 req/s | 19.4 ms |
| v2 `/tickets` (PostgreSQL) | 27.6 req/s | 33.9 ms |
| v2 `/classify` (no database) | 50.0 req/s | 16.1 ms |

A durable write costs about +14.5 ms. In exchange: data survives restarts, processes share it,
and 3,006 concurrent inserts produced 3,006 distinct ids.

One unexpected result: raising concurrency from 1 to 10 raised throughput from 27.6 to
44.5 req/s in a single process — waiting on the database releases the GIL, so inference in
another thread can overlap with it. In v0 and v1, concurrency only added queueing.

**Known limitation, measured:** `GET /tickets` has no paging. With 13,006 rows it returned all
of them and took 2.18 s.

## Documents

- Full version document: [v2-postgresql.md](../v2-postgresql.md)
- [ADR-007 — PostgreSQL as the system of record](../../adr/ADR-007-postgresql-system-of-record.md)
- [ADR-008 — SQLAlchemy ORM with synchronous sessions](../../adr/ADR-008-sqlalchemy-orm-sync-sessions.md)
- [ADR-009 — Alembic migrations from the first table](../../adr/ADR-009-alembic-migrations.md)
- Previous version: [v1 — Modular Monolith](../v1-modular-monolith.md)
