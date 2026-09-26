# ADR-013 — The organisation is the tenancy boundary, and its filter is a required argument

Status: accepted (v4)

## Context

Once callers can be identified (ADR-012), the next question is what they are allowed to see.
Before v4 the answer was "everything", because no row recorded an owner.

This decision has to be made now rather than later for a reason that is not about today's
requirements. v5 introduces documents and v9 introduces retrieval over them. Retrieval that
crosses an ownership boundary is a data breach, not a bug — and the shape of the boundary has to
exist before anything is built on top of it. Adding an owner column to a table with 13,000 rows is
a migration; adding it to one with 10 million is a backfill plus an audit of every query written
without it.

## Options

**What owns data:**

1. **A user owns their tickets.** `tickets.user_id`. Simple, and wrong for a support desk: when an
   agent leaves, the tickets must stay and remain readable by their colleagues.
2. **An organisation owns tickets; users belong to an organisation.** One extra table, and it
   matches how support desks actually work.
3. **Both, immediately** — organisation for visibility plus per-user roles. Correct eventually,
   more than any measured requirement needs today.

**How the boundary is enforced:**

4. **An `if` in each endpoint.** `if ticket.organization_id != current_user.organization_id:
   raise 404`. Readable, and it depends on every present and future handler remembering.
5. **An optional filter on the repository**, `organization_id: int | None = None`. Convenient for
   tests and admin scripts.
6. **A required filter on the repository**, `organization_id: int` with no default, applied
   unconditionally in the shared `_filtered()` helper.
7. **PostgreSQL Row-Level Security.** The database enforces it regardless of the query, which is
   the strongest option available. It needs a per-request `SET` of a session variable, interacts
   with connection pooling, and is invisible in the Python code.

## Decision

Option 2 for ownership, option 6 for enforcement.

- `organizations` and `users` tables; `users.organization_id` and `tickets.organization_id` are
  both `NOT NULL` with foreign keys.
- `organization_id` is the **first, required, defaultless parameter** of every read on
  `TicketRepository` and `TicketService`: `get`, `list`, `count`.
- In `PostgresTicketRepository._filtered()` the organisation condition is applied with **no `if`
  in front of it**, unlike `label` and `needs_review`.
- The service sets `Ticket(organization_id=organization_id, ...)` from the authenticated caller.
  `CreateTicketRequest` has no `organization_id` field.
- The in-memory test double applies the same filter, so a service-level test cannot pass while
  isolation is broken.

## Why?

- **A required argument cannot be forgotten; an optional one can.** This is the entire decision:

  ```python
  def list(self, organization_id: int | None = None, ...)   # forget it → every tenant's rows
  def list(self, organization_id: int, ...)                 # forget it → TypeError
  ```

  Option 5's failure mode is a silent cross-tenant leak that passes every existing test. Option
  6's failure mode is a crash at the call site, in the first run. **Make the unsafe version
  impossible to write rather than discouraged.**
- **Option 4 was rejected because it scales with discipline rather than with structure.** An `if`
  in a handler protects the handlers that have it. A required repository argument protects every
  caller including the ones not written yet, and pushes the condition into SQL where PostgreSQL
  does the work.
- **No `if` on the organisation condition, because there is no legitimate caller who wants every
  organisation's rows.** `label` and `needs_review` are genuinely optional filters. This is not
  one, and writing it the same way as the others would have implied that it is.
- **The organisation comes from the verified token, never from the request body.** A client able
  to send its own `organization_id` could write into any account, and the boundary would be
  decoration. Pydantic ignores unknown fields by default, so an injected `organization_id` is
  silently dropped — which is why there is a test asserting the ticket still lands in the token's
  organisation. Silence is not proof.
- **Row-Level Security (option 7) is strictly stronger and was still declined.** It requires
  setting a session variable per request, which fights the connection pool the project already
  measured and tuned, and it moves the rule out of the code being learned from into database
  configuration. The trade is real and recorded here rather than dismissed: RLS defends against a
  developer writing raw SQL that bypasses the repository, and option 6 does not.
- **Organisation-only, no roles yet (option 3 declined).** Every member of an organisation sees
  everything it owns. No measured requirement distinguishes members, and inventing roles now would
  be exactly the speculative complexity this project exists to avoid.

## Trade-offs

Gain: the boundary is enforced in SQL for every caller; a forgotten filter is a crash rather than
a leak; the cost is measurable and was measured; ownership exists before any data accumulates.

Lose:

- **`session.get()` became unusable.** It looks up by primary key only, with no room for a second
  condition, so `get()` had to become an explicit `select()`. Adding a boundary made a convenience
  method unavailable — a small, concrete instance of why retrofitting security costs more than
  building with it.
- **Every implementation of the protocol had to change by hand.** `PostgresTicketRepository`,
  `InMemoryTicketRepository`, and the inline `BrokenRepository` double. A `Protocol` has no runtime
  enforcement, and `BrokenRepository` broke for the **second consecutive version** for this exact
  reason. The strongest argument in this repository for adding mypy or Pyright.
- **A new list endpoint must remember the rule.** Documents in v5 will need it. Nothing enforces
  that automatically — the same standing obligation ADR-011 created for page-size ceilings.
- **The 13,000 existing tickets were backfilled into one `default` organisation.** They are
  intact, but they now belong to a synthetic tenant nobody logs into. Honest consequence of adding
  ownership after the fact, and visible: a freshly registered user sees `total: 0`.
- **No per-user visibility and no audit of who did what.** `needs_review` still means "somebody
  should look", with no record of who did.

## Future Trigger

- **Add roles inside an organisation** when a requirement distinguishes members — an agent who may
  review versus one who may only submit.
- **Add `reviewed_by_user_id`** when "who approved this ticket's routing?" has to be answerable.
  That is the audit trigger, and it is the first thing that makes `users` more than a login table.
- **Reconsider Row-Level Security** if the repository stops being the only path to the data —
  a reporting script, an admin tool, or anything issuing raw SQL. At that point discipline is no
  longer sufficient and the database should hold the rule.
- **Add a composite index** `(organization_id, id)` or `(organization_id, label)` when a single
  organisation's slice stops being the whole table. Today every row is in `default`, so the
  planner correctly ignores the organisation index entirely — recorded in the v4 measurements. The
  right index depends on a data distribution that does not exist yet.
- **Extend the required-filter rule to documents, answers and every future owned entity**, as each
  is added.
