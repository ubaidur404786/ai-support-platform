# ADR-021 — Use the documents table as the job queue

Status: accepted (v7)

## Context

In v6, extraction and chunking ran inside the upload request: 7.8–8.1 s for a 300-page PDF and
10.3–11.3 s for a 4.9 MB text file (P50, two rounds in the v7 session). While four large PDFs were
processing, a `GET /health` from someone else took **P50 335–729 ms instead of 15–18 ms**, and up
to 3.3 s. Python runs only one thread of Python code at a time (the GIL), so CPU work in one request
slows every other request in the same process.

The work has to leave the request. That needs three things: somewhere the job waits, something
that runs it, and a way for the client to find out when it is done.

## Options

1. **A table in PostgreSQL as the queue**: the document's own `status` column (`queued` →
   `processing` → `ready` / `failed`). A worker process claims rows with
   `SELECT ... FOR UPDATE SKIP LOCKED`.
2. **Redis + a task library** (RQ, Celery, Dramatiq).
3. **A message broker** (RabbitMQ, Kafka).
4. **FastAPI `BackgroundTasks`**: run the work after the response is sent, in the same process.

## Decision

Option 1. `app/documents/processing.py` holds the job logic; `app/worker.py` is a plain loop
started with `python -m app.worker`. The upload answers `202 Accepted` with `status: "queued"`, and
the client polls `GET /documents/{id}`.

## Why?

- **It adds no new service.** PostgreSQL is already running and already holds the document. A
  second system (Redis) would need running, monitoring and backing up, and there is no measured
  need for it.
- **The document and its job are the same row, written in the same transaction.** With Redis, the
  row is committed in PostgreSQL and the job is pushed to Redis in a separate step. A crash between
  them leaves a document that is never processed, or a job for a document that does not exist.
  That is the dual-write problem, and it does not exist here.
- **`SKIP LOCKED` makes several workers safe.** A worker that finds a locked row skips it and takes
  the next one. It never waits for it and never takes it as well. `test_two_workers_never_take_the_same_document`
  proves this.
- **Option 4 does not solve the problem.** `BackgroundTasks` runs in the same process, so the CPU
  work still competes with requests for the GIL. A job in flight is also lost if the process
  restarts.
- **Status is visible to the user for free.** `queued`, `processing`, `ready` and `failed`, with a
  reason, are ordinary columns the API already returns.

## Result

Upload answer 154–217 ms (PDF) and 775–854 ms (4.9 MB text). `GET /health` P50 during the same
4-PDF load: 17–21 ms. Document-ready time is unchanged (the same work, done later).

## Trade-offs

Gain: no new infrastructure; one transaction for document + file + job; crash recovery using
columns anyone can read with SQL.

Lose:

- **Polling.** An idle worker asks "anything queued?" once per second (one indexed query), and a
  new upload waits up to one second before the worker notices it. PostgreSQL `LISTEN/NOTIFY` could
  wake the worker at once. That is worth doing when a second of delay matters. It does not yet.
- **A crash is recovered by timeout.** A worker that dies mid-document leaves it `processing`. It
  is put back in the queue once it has been `processing` for more than 5 minutes
  (`WORKER_STALE_AFTER_SECONDS`), so the user waits those 5 minutes. A worker that is only slow,
  not dead, is handled by the `attempts` check, which throws away its late result.
- **Everything is built by hand.** Retries, backoff, scheduling and a dashboard are what task
  libraries provide. We have retries (3 attempts, unexpected errors only) and nothing else.
- **The queue's load falls on the main database.** At a few uploads a minute it is negligible. At
  thousands of jobs a second it would not be.

## Future Trigger

- **Many kinds of job** (embeddings, re-indexing, notifications) with priorities, schedules or
  chains → a task library. Redis is likely to be introduced anyway for the shared rate limits.
- **Job throughput** large enough that polling and row locks show up in database load.
- **More than one consumer per event** (an upload that should trigger indexing *and* analytics *and*
  a notification) → an event system (v22).
