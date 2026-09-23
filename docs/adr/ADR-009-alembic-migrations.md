# ADR-009 — Versioned migrations from the first table

Status: accepted (v2)

## Context

Introducing PostgreSQL raises a question that is easy to postpone and expensive to answer late:
how does the database schema come into existence, and how does it change afterwards?

The roadmap makes the second half concrete. Documents, answers, and authentication each add
tables or columns. The schema will change at least three more times.

## Options

1. **`Base.metadata.create_all()` at startup.** One line. Creates any table that does not exist
   and does nothing else — it cannot add a column, change a type, or add a constraint to an
   existing table.
2. **Hand-written SQL scripts**, applied in order by convention. Full control, no dependency.
   Nothing tracks which scripts have run, so that becomes a human responsibility.
3. **Alembic.** Migration files with an explicit order, a table in the database recording which
   have been applied, autogeneration by comparing models against the live schema, and a
   downgrade path.

## Decision

Alembic, from the very first table. `alembic/env.py` takes the database URL from the
application's settings rather than from `alembic.ini`, so credentials live in one place.
`target_metadata` points at `Base.metadata`, which makes `--autogenerate` work.

The test suite builds its database by running the real migrations, not `create_all()`.

## Why?

- `create_all()` works exactly once. The first schema change means dropping the database and
  losing its contents, and retrofitting migrations afterwards means writing a first migration
  that reconciles with hand-created tables.
- Alembic records applied revisions in an `alembic_version` table, so "has this run?" is a fact
  in the database rather than something a person remembers.
- Autogenerate produces a starting point quickly — the first migration correctly inferred
  `nullable=False` on every column from the `Mapped[...]` annotations, and created the index.
- Running migrations in tests catches the most common failure in this area: a model changed and
  the migration was never written. Without that, tests would pass against a schema no deployment
  would ever have.

For a prototype that will be thrown away, `create_all()` is the right answer. This is not that.

## Trade-offs

Gain: a schema whose history is explicit and repeatable, a deployment step that can be verified,
a downgrade path, and tests that exercise the same migrations production would run.

Lose:

- **A new dependency and a new concept** to learn before the first table exists.
- **A deployment step that can fail.** Migrations must run before the application starts, and a
  failed migration is an outage.
- **Autogenerate is an assistant, not an authority.** It misses some changes (server defaults,
  some constraint edits, anything involving data) and occasionally proposes destructive
  operations. Every generated migration must be read before it is applied.
- **Slower tests.** The suite went from about 7 s to about 28 s, partly from running migrations
  and partly from truncating tables over a connection.
- **Migrations that move data are genuinely hard**, and autogenerate does not help at all with
  those. That difficulty is deferred, not avoided.

## Future Trigger

- **Write migrations by hand** whenever a change involves existing data — backfilling a new
  column, splitting a table, changing a type with a conversion. Autogenerate handles structure,
  never content.
- **Reconsider how migrations run** once deployment becomes automated: running them from a
  container entrypoint means every replica races to apply them, so the usual answer is a
  separate job that completes before the new version starts. That belongs with the CI/CD work.
- **Expect zero-downtime constraints** when the system can no longer be stopped for a
  deployment. That forces expand-and-contract migrations — add a column, write to both, migrate
  readers, then drop the old one — which is a discipline, not a tool change.
