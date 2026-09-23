# ADR-005 — Repository pattern, with an in-memory implementation for now

Status: accepted (v1) · superseded in part when persistent storage arrives

## Context

v1 introduces the first thing the platform must remember: a submitted ticket. Until now
nothing was stored, so there was no storage decision to make.

The project's rule is to introduce a technology only when the previous design has a real
problem. A database is the obvious destination, but v1's actual requirement is narrow: store a
ticket, read it back, list with two filters. The interesting question is not "which database"
but "how does the rest of the code talk to storage, so that answering the database question
later is a local change".

## Options

1. **Dictionary accessed directly from the service or router.** Least code. Storage details
   leak into business logic, so moving to a database later means editing every call site.
2. **Repository class wrapping a dictionary.** All storage access behind `add` / `get` /
   `list`. One more class today; the database swap becomes one file.
3. **SQLite immediately.** Real persistence, no server to run, standard library. Brings an
   ORM or SQL, migrations, and a session lifecycle before there is a demonstrated need.
4. **PostgreSQL immediately.** The eventual answer, but it introduces a service to run,
   connection pooling, migrations, and test fixtures in the same version as a structural
   refactor — two large changes at once, with no measurement separating their effects.

## Decision

Option 2. `InMemoryTicketRepository` holds a `dict[int, Ticket]`, generates ids, and exposes
`add`, `get`, `list`, `count`. The service receives a repository; the router never touches
storage. One repository instance is created per application at startup.

Id generation is guarded by a `threading.Lock`, because FastAPI runs synchronous endpoints in
a thread pool and two requests can reach the repository simultaneously.

## Why?

- It keeps v1 about structure. The database arrives in its own version, where its cost and
  benefit can be measured on their own.
- The interface is the part that matters, and it is being designed now: the service already
  treats storage as something that can fail to find a ticket (`get` returns `None`) and that
  assigns ids (`add` sets `ticket.id`). Both assumptions hold for a database.
- It makes the limitation visible rather than theoretical. Two servers were started on
  different ports; a ticket posted to one was invisible to the other. That demonstration is
  the argument for the next version.
- Tests get a genuinely clean store per test by constructing a new application, with no
  fixtures, no truncation, and no transaction rollback machinery.

## Trade-offs

Gain: business logic that does not know how tickets are stored; a database swap that touches
one file; fast, isolated tests.

Lose:

- **No persistence.** A restart loses every ticket.
- **No sharing between processes.** Measured: `POST /tickets` on port 8000 returned `id 1`,
  while `GET /tickets` on port 8001 returned `total 0`. Two replicas generate duplicate ids
  and serve reads that miss recent writes.
- **This blocks the cheapest fix for v0's throughput ceiling.** Running more uvicorn workers
  would raise the ~55 req/s limit, but cannot be done while each worker owns its own store.
- **`list()` is a full scan.** Every ticket is loaded into a Python list and filtered in
  memory. Correct at three tickets, useless at a hundred thousand.
- One extra class and one extra indirection for a feature that could have been a dictionary.

## Future Trigger

This decision is already at its trigger — the constraints above were measured in v1 rather
than predicted. It is kept only because v1's scope is structural.

Replace the implementation when the next version starts. The expected step is PostgreSQL
behind the same interface (planned as v2), which addresses persistence, cross-process sharing,
real queries with indexes and paging, and unblocks running multiple workers.

The interface itself should be revisited if it turns out to leak database concerns upward —
for example if callers need transactions spanning several repository calls, which the current
method-per-operation shape does not express.
