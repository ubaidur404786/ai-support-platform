# v4 — Authentication and tenant isolation

Data now has an owner. A token identifies the caller, and every ticket belongs to exactly one
organisation — enforced in SQL, not in handler code.

![v4 architecture](../../architecture/v4.svg)

## What this version adds

- `organizations` and `users` tables; `tickets.organization_id` as `NOT NULL` with a foreign key
- `POST /auth/register` and `POST /auth/login` — bcrypt hashing, a signed JWT on success
- `get_current_user`, a dependency that runs **before** the handler body, so an anonymous caller
  never reaches endpoint code
- `organization_id` as a **required** first parameter on every service and repository read —
  forgetting it is a `TypeError`, not a silent leak
- Another organisation's ticket returns **404, not 403**: across a tenant boundary, existence is
  itself information

Deliberately **not** protected: `POST /classify`. It stores nothing and owns no data — but it
does burn CPU, which is a denial-of-service surface. Recorded as an open gap, not an oversight.

## Run

```bash
docker compose up -d
```

```bash
alembic upgrade head
```

`JWT_SECRET_KEY` has no default — the app refuses to start without it. Copy `.env.example` to
`.env` and generate one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```bash
uvicorn app.main:app
```

## Try it

```bash
curl -X POST http://127.0.0.1:8000/auth/register -H "Content-Type: application/json" -d '{"organization_name": "Acme", "email": "maya@acme.com", "password": "correct-horse-battery"}'
```

```bash
curl -X POST http://127.0.0.1:8000/auth/login -H "Content-Type: application/json" -d '{"email": "maya@acme.com", "password": "correct-horse-battery"}'
```

```bash
curl -i http://127.0.0.1:8000/tickets
```

The last one returns **401** `{"detail":"Not authenticated"}` with a `WWW-Authenticate: Bearer`
header. That 401 is the whole version — yesterday it was a 200.

## Test

```bash
pytest -v
```

99 tests. The two worth reading:

`test_a_ticket_is_invisible_to_another_organisation` — Acme writes a ticket, Globex gets 404 for
it by id and `total: 0` from the list, and Acme can still read it. That last assertion matters:
it proves the filter is *scoped*, not merely broken.

`test_tickets_endpoints_require_a_token` — parametrised over every `/tickets` route rather than
written once, because the risk is not "auth is broken", it is "one endpoint was added without it".

## Results (measured locally, 13,000 tickets, `DB_ECHO=false`, no `--reload`)

| | Measured |
|---|---|
| `bcrypt.checkpw` alone, cost factor 12 | 682.2 ms |
| `POST /auth/login` P50, n=10 | **682 ms** — the endpoint *is* the hash |
| `GET /tickets` P50, n=20 | 33.8 ms |
| `users` lookup added by `get_current_user` | **0.183 ms** — 0.5% of the request |
| tickets page, no filter (warm) | 0.145 ms |
| tickets page, organisation filter (warm) | **0.128 ms** — the filter is free |

**Three findings worth carrying forward:**

The planner **declined** `ix_tickets_organization_id` for the page query, using `tickets_pkey`
with a row filter instead. Correct: every row matched, so the index helped nothing. An index is
a possibility the planner may decline, not an instruction.

The same index turned `count(*)` into an `Index Only Scan` with `Heap Fetches: 0` — 5.78 ms
against v3's 22.3 ms. **No speedup is claimed** (different session), but the plan is structurally
better. An index added for security incidentally improved the counter, which was not predicted.

**Login is now the most expensive endpoint in the system**, ~30× a ticket write, and reachable
without credentials. This version created a denial-of-service surface. Rate limiting is the fix.

The test suite went from ~7 s to 102.92 s. That is not overhead — it is ~45 fixtures × 1.36 s of
bcrypt ≈ 61 s, about 60% of the runtime. A hash fast enough to be free in tests is a hash fast
enough to brute-force in production.

## Documents

- Full version document: [v4-authentication.md](../v4-authentication.md)
- [ADR-012 — Bearer tokens for authentication](../../adr/ADR-012-bearer-tokens.md)
- [ADR-013 — Organisation-scoped authorization](../../adr/ADR-013-organisation-scoped-authorization.md)
- [ADR-014 — bcrypt for password storage](../../adr/ADR-014-bcrypt-password-storage.md)
- [ADR-015 — Cross-tenant reads answer 404, not 403](../../adr/ADR-015-cross-tenant-404.md)
- Previous version: [v3 — Pagination](../v3-pagination.md)
