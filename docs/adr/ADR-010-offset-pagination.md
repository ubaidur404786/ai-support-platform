# ADR-010 — Offset pagination for list endpoints

Status: accepted (v3)

## Context

`GET /tickets` returned every matching row. Measured with 13,000 tickets: 2.18 seconds, around
13,000 Pydantic objects, and one very large response. The endpoint's cost was a function of the
data rather than of the request, so it would get worse exactly as the system became more
successful.

A pagination scheme has to answer two questions: how does a client ask for the next window, and
what does the database have to do to produce it.

## Options

1. **Offset pagination** — `LIMIT n OFFSET m`. The client asks for "50 rows starting at row
   100". Supports jumping to an arbitrary page. The database must locate and discard `m` rows
   before returning any, so cost grows with depth. Rows inserted during a walk shift the
   windows, so a row can be seen twice or skipped.
2. **Keyset (cursor) pagination** — `WHERE id > last_seen ORDER BY id LIMIT n`. The database
   seeks straight to the position using an index, so cost is constant regardless of depth, and
   inserts do not shift already-fetched pages. No random page access: a client can go forward
   (and backward, with care), but not to "page 400" directly.
3. **Streaming the whole result** — chunked response. Fixes memory, not latency, and leaves the
   caller controlling how much work the server does.
4. **A hard cap with no paging** — return at most 1,000 rows. Simple, but rows beyond the cap
   become unreachable.

## Decision

Offset pagination, with `ORDER BY id` as the sort key. The response carries `total`, `limit`,
`offset` and `items`. The repository's `list()` and `count()` share one `_filtered()` helper so
they can never apply different conditions.

## Why?

- It solves the measured problem completely: a page of 50 executes in 0.36 ms against 13,000
  rows, and the work no longer grows with table size.
- Keyset pagination is better at depth, but `OFFSET` being slow at depth had not been measured
  when the choice was made. Choosing the more complex design first would have been guessing.
  The measurement now exists (below), so the switch has a trigger rather than an opinion behind
  it.
- Offset supports "jump to page N", which a support queue UI plausibly wants and keyset cannot
  provide.
- `ORDER BY id` is not decoration. Without a deterministic sort, "the first 50 rows" is whatever
  the database happens to return, and consecutive pages can overlap or skip rows. `id` is
  immutable and unique, which makes it safe.

## Trade-offs

Gain: bounded responses, latency independent of table size, a client contract that can express
"how many are there" alongside "here is a window".

Lose:

- **Deep pages do work they discard.** Measured: `OFFSET 0` walked 50 index entries in 0.360 ms;
  `OFFSET 12900` walked 12,950 in 6.171 ms — 17× the work for the same 50 rows, growing linearly
  in `offset + limit`. At this table size it is invisible through HTTP (91.8 ms vs 89.6 ms
  end to end), which is why it is a recorded future problem rather than a present one.
- **`total` requires a second query, and it dominates.** Measured at 22.3 ms execution against
  0.36 ms for the page — roughly 60 to 1. PostgreSQL cannot store a row count, because under
  MVCC the number of visible rows depends on the asking transaction. This contradicted the
  expectation held when the feature was designed.
- **Pages shift under concurrent writes.** An insert during a client's walk moves rows between
  windows. Sorting by an immutable id limits the damage but does not remove it.
- **Clients must loop** to retrieve everything.
- **The test double hides the depth cost.** The in-memory fake implements offset as a Python
  slice, which is instant, so no test against it can reveal `OFFSET` degradation.

## Future Trigger

**Switch to keyset pagination when** deep offsets appear in real traffic, or the table grows
large enough that the measured degradation becomes visible end to end. The mechanism is
understood and the cost is linear in offset, so the crossing point can be predicted rather than
discovered in production. Expect to keep offset paging alongside it if the UI needs page jumps.

**Revisit `total` sooner than that** — it is the larger cost today. Options, in rough order of
effort: make it optional (`?include_total=false`), cache it per filter combination, use
PostgreSQL's planner estimate (`reltuples`) for large tables, or replace the exact count with a
"more results available" flag.

**Revisit the sort order** as a product question: ascending id puts the oldest tickets first,
which is rarely what a support queue wants. Changing it interacts with pagination stability, so
it is not a one-line change.
