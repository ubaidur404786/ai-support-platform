# ADR-011 — Every request must do a bounded amount of work

Status: accepted (v3)

## Context

Before v3, `GET /tickets` did an amount of work chosen entirely by the data: 13,000 rows meant
2.18 seconds of server time for one request. With measured throughput around 44 req/s, a
handful of such requests occupies every worker thread and the API stops responding to anyone.

Nothing in the request said how much to return, and nothing in the code could have refused.
That is not only a performance characteristic — it is an availability risk, reachable by any
client, deliberately or by accident. A retry loop with a bug is enough.

Pagination (ADR-010) provides the mechanism. This decision is about the policy: who is allowed
to decide how much work the server does, and where that is enforced.

## Options

1. **A documented recommendation.** Tell clients to pass a sensible `limit`. Costs nothing,
   protects nothing: it relies on every caller being correct forever.
2. **A default only.** A client that passes nothing gets 50 rows; a client that passes
   `limit=1000000` gets a million. Protects the well-behaved and nobody else.
3. **A default plus a ceiling, enforced at the HTTP edge.** FastAPI rejects an oversized limit
   with 422. Protects every HTTP caller.
4. **A ceiling enforced at the edge and again in the service.** Also protects callers that never
   pass through HTTP.
5. **Silently clamping** an oversized request to the maximum. The server is protected, but the
   client is told nothing and may conclude the dataset is smaller than it is.

## Decision

Option 4, with option 5 explicitly rejected.

- `default_page_size` (50) and `max_page_size` (200) are configuration, not constants.
- The router declares `Query(ge=1, le=settings.max_page_size)`, so FastAPI returns **422 before
  the handler runs** and an oversized page is never constructed.
- `TicketService.list()` applies the default when no limit is given and clamps
  `max(1, min(limit, max_page_size))` regardless of who called it.
- `TicketPage` carries `limit` and `offset` back to the caller, so the applied window is always
  visible in the response.

## Why?

- **The caller must not choose the server's workload.** This is the whole decision in one
  sentence. Every unbounded input is a capacity decision handed to someone outside the system.
- **Enforcing at two layers is defence in depth, not duplication.** The router protects HTTP
  callers. The service protects itself. When v5 introduces a background worker calling
  `service.list()` directly, there is no FastAPI in the path — a limit enforced only at the edge
  would silently cease to exist. A boundary defended on one side is defended until someone walks
  around it.
- **422 rather than silent clamping**, because a client that asked for 500 and received 200
  without being told may reasonably conclude the table holds 200 rows. Being explicit about a
  rejected request is cheaper than debugging a client that draws wrong conclusions.
- **Configuration rather than constants**, because the right page size is operational: it
  depends on the client, the payload size, and how much review capacity exists.
  `MAX_PAGE_SIZE=100` changes the policy without deploying code.

## Trade-offs

Gain: a per-request cost with a known upper bound, an availability risk removed rather than
mitigated, and a limit that survives new kinds of caller.

Lose:

- **The rule must be applied to every new list endpoint**, and nothing enforces that
  automatically. Documents and answers will each need it, and forgetting reintroduces the
  problem at a new URL.
- **Two places to change** when the ceiling changes, though both read the same setting.
- **A legitimate bulk consumer is inconvenienced.** An export job now loops. The right answer
  there is a purpose-built export path with its own constraints, not a raised ceiling.
- **The bound is on row count, not on response size.** Two hundred tickets with 5,000-character
  bodies is a megabyte. Row count is a proxy for work, and a rough one.

## Future Trigger

- **Extend the rule to every new collection endpoint** as it is added. This is a standing
  obligation, not a one-off change.
- **Bound other resources when they appear**: request body size, file upload size, query
  timeouts, and the number of documents a retrieval request may fetch. Each is the same
  decision in a different unit.
- **Add rate limiting** when bounded-but-frequent requests become the problem. Pagination caps
  the cost of one request; it does nothing about a thousand of them per second, and this ADR
  should not be mistaken for protection against that.
- **Reconsider the proxy** if response size rather than row count turns out to be what hurts —
  the ceiling would then need to account for payload, not just cardinality.
