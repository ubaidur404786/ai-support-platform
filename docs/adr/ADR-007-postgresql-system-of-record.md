# ADR-007 — PostgreSQL as the system of record

Status: accepted (v2) · supersedes [ADR-005](ADR-005-repository-pattern-in-memory.md)

## Context

v1 stored tickets in a dictionary inside the API process. Its limits were measured, not
predicted: a ticket posted to a server on port 8000 was invisible to a server on port 8001,
and every ticket was lost on restart.

The consequence went beyond data loss. Inference is CPU-bound and Python's GIL prevents threads
from adding capacity, so the standard remedy for the measured throughput ceiling is more worker
processes. That was unavailable: each worker would own a separate store, generate ids from its
own counter, and serve reads that miss another worker's writes.

## Options

1. **Keep the in-memory store.** Fails durability, sharing, and querying.
2. **JSON file on disk.** Durable in the weakest sense. Every write rewrites the whole file,
   concurrent writers corrupt it, and there is no way to query without loading everything.
3. **SQLite.** Real SQL, durable, no server to run, in the standard library. With WAL mode it
   even supports multiple processes on one machine. It does not support multiple machines,
   high write concurrency, or the containerised deployment this roadmap heads toward.
4. **PostgreSQL.** A separate server process. Durable, shared by any number of clients on any
   number of machines, with real transactions, indexes, and query planning.
5. **A document database (MongoDB and similar).** Flexible schema. Tickets are strongly
   structured and will gain relationships to documents and answers, which is what relational
   databases are for.

## Decision

PostgreSQL 17, run locally through Docker Compose, accessed through the `TicketRepository`
interface defined in v1. `PostgresTicketRepository` is the only production implementation; the
in-memory version moves to `tests/fakes.py` as a test double.

Primary keys come from a database sequence (`tickets_id_seq`), never from application code.

## Why?

- It removes all three measured limitations at once, and unblocks horizontal scaling in
  principle: any number of API processes can share one database.
- Ids from a sequence make duplicates impossible across processes. Verified: 3,006 rows written
  by concurrent requests produced 3,006 distinct ids.
- It is free and runs locally in a container, so it respects the project's no-paid-cloud
  constraint while matching what production would actually use.
- The repository interface meant the change was contained: one new file, one type hint in the
  service, no change to the router or to business logic. The service tests passed with a single
  import changed.

SQLite was the serious alternative and was rejected on trajectory, not on capability. For a
single-user desktop application it would be the better answer.

## Trade-offs

Gain: durability, sharing between processes, sequence-generated ids, indexed queries, real
transactions, and a stateless API.

Lose:

- **Latency.** Measured: `/tickets` went from 48.6 req/s / P50 19.4 ms to 27.6 req/s /
  P50 33.9 ms — about +14.5 ms per durable write. `/classify`, which writes nothing, was
  unchanged at 50.0 req/s.
- **An external dependency that can fail.** Handled explicitly: a failed query is rolled back
  and translated into `StorageError`, which becomes HTTP 503; `/health` reports
  `database_reachable` and downgrades its status rather than hiding the problem.
- **Connection pooling becomes a budget.** Each worker process holds its own pool, so
  workers × (`pool_size` + `max_overflow`) must stay under PostgreSQL's connection limit.
- **Operational weight.** A second process must be running, `docker run` alone no longer starts
  the system, and tests need a database.

## Future Trigger

Revisit when:

- Read volume outgrows a single instance — the usual next steps are read replicas, then caching
  (planned separately), not a different database.
- A workload appears that relational storage serves poorly. Vector search for retrieval is the
  concrete one on this roadmap; the likely answer is `pgvector` in the same database rather
  than a second system, and that choice should be measured rather than assumed.
- Write volume exceeds what one primary can absorb, which would raise partitioning or sharding.
  Nothing measured so far comes close.

Not a trigger: the +14.5 ms write latency. That is the price of the guarantees, and the
measured alternative loses data.
