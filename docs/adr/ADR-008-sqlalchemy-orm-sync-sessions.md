# ADR-008 — SQLAlchemy ORM, synchronous sessions, and where the transaction boundary sits

Status: accepted (v2)

## Context

With PostgreSQL chosen (ADR-007), three questions follow: how does Python talk to it, whether
that happens synchronously or asynchronously, and who decides when a transaction commits.

The existing design constrains the second answer. Endpoints are declared `def`, not `async def`,
deliberately: model inference is CPU-bound, and FastAPI runs synchronous endpoints in a thread
pool so they do not block the event loop.

## Options

**Talking to the database**

1. **Raw SQL through psycopg.** No extra dependency, full control, every statement visible.
   Every row must be mapped to an object by hand, and every query maintained by hand.
2. **SQLAlchemy Core.** Builds SQL from Python expressions without objects. Less magic than the
   ORM, but still manual mapping.
3. **SQLAlchemy ORM (typed 2.0 style).** Objects map to rows; the library generates the SQL.

**Synchronous or asynchronous**

4. **Async SQLAlchemy with async endpoints.** The common recommendation for FastAPI.
5. **Synchronous SQLAlchemy with the existing sync endpoints.**

**Transaction boundary**

6. **Commit in the session dependency**, after the endpoint returns.
7. **Commit in the service**, so a business operation is a transaction.
8. **Commit in the repository**, so one storage operation is a transaction.

## Decision

The ORM (option 3), synchronous (option 5), committing in the repository (option 8).

Database failures are caught in the repository, rolled back, and re-raised as `StorageError` —
one exception type the upper layers own. The router turns that into HTTP 503.

## Why?

**The ORM** because tickets will gain relationships to documents and answers, which is what an
ORM handles well, and because the typed 2.0 style (`Mapped[int]`, `mapped_column`) gives real
type checking and lets the migration tool infer nullability from the annotations.

**Synchronous**, and this is the decision most likely to be questioned. Async database access
would require async endpoints, and then `classifier.predict()` — CPU work — would run on the
event loop and block every other request for its duration. Async pays off when a service is
I/O-bound with many mostly idle connections. This service is CPU-bound with a small I/O
component. Staying synchronous keeps inference in the thread pool where it belongs.

The measurements support this. With the database added, raising concurrency from 1 to 10 in a
single process raised throughput from 27.6 to 44.5 req/s: a thread waiting on the database
releases the GIL, so another thread runs inference meanwhile. The thread pool is already
overlapping I/O and CPU.

**Committing in the repository** because the alternatives are worse right now. Committing in
the session dependency puts the commit after the response has been built, so a commit failure
could not change a status code already decided. Committing in the service makes business logic
aware of persistence. Every request currently writes at most one thing, so one operation being
one transaction is accurate.

**Translating errors** because without `StorageError` the router would need
`except SQLAlchemyError`, which means the HTTP layer imports the database library, and swapping
the database would touch every layer. The translation also rolls back: a failed statement
leaves the session unusable until it does, and the next query would fail for the wrong reason
and hide the real cause.

## Trade-offs

Gain: little hand-written SQL, typed models, one place where database failures become
application errors, and a design where CPU-bound inference and database I/O overlap correctly.

Lose:

- **The generated SQL is hidden.** An ORM makes inefficient queries easy to write by accident.
  `db_echo` exists to print statements while learning, and `EXPLAIN ANALYZE` remains the tool
  that tells the truth.
- **Synchronous means a thread per in-flight request.** The thread pool has a limit; a service
  with thousands of idle connections would need the async model.
- **One transaction per repository call.** Two writes in one request cannot yet be made atomic.
- **The engine is created at module import**, bound to settings loaded at import. Tests must set
  `DATABASE_URL` before importing anything from `app`. That ordering requirement is a real smell.

## Future Trigger

- **Revisit the ORM** if a query is measured to be slow and the generated SQL is the cause;
  SQLAlchemy allows dropping to Core or raw SQL for that one query without abandoning the rest.
- **Revisit synchronous** if the service becomes I/O-bound — for example once inference moves to
  a separate model service and the API mostly waits on network calls. At that point async
  endpoints stop conflicting with CPU work, and the trade-off reverses.
- **Move the transaction boundary** the first time one request must write two rows atomically.
  The expected shape is a unit-of-work owned by the service or by a request-scoped dependency.
- **Build the engine inside `create_app`** to remove the import-ordering requirement in tests;
  worth doing alongside any other change to application startup.
