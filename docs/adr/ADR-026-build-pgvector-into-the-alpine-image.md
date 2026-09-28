# ADR-026: Build pgvector into our own PostgreSQL Alpine image

Status: accepted (v9)

## Context

pgvector ([ADR-025](ADR-025-pgvector-hnsw-index.md)) is a PostgreSQL extension. The server has to
have it installed before `CREATE EXTENSION vector` can work. Since v2, the project had run the
official `postgres:17-alpine` image, and that image does not include pgvector. The development data
volume already held about 120,000 chunks and several measurement databases.

## Options

1. **Switch to the `pgvector/pgvector:pg17` image.** It is ready-made, but it is based on Debian,
   not Alpine.
2. **Build our own image**: `FROM postgres:17-alpine`, then compile pgvector into it.
3. **Install pgvector by hand** inside the running container. That is lost the next time the
   container is recreated.

## Decision

Option 2: `docker/postgres/Dockerfile`, pinned to pgvector 0.8.6. `docker-compose.yml` builds it as
`ai-support-db:pg17-pgvector` and keeps the same data volume.

## Why?

The databases were created with collation `en_US.utf8` (checked with `pg_database`). Alpine
implements that collation with musl, and Debian implements it with glibc. The two **sort text
differently**. An index on a text column (`users.email`, `organizations.name`, `documents.sha256`)
stores its rows in sorted order. If a server using the other library opens that index, it looks for
rows in the wrong places, so lookups can silently miss rows and unique constraints can fail to
fire. Switching the image under an existing volume would have needed a dump and restore, or a
reindex of every text index. Adding the extension to the image we already run changes nothing that
is on disk.

Build details, each one found while building:

- `with_llvm=no`: the Alpine image was built with LLVM JIT support, so pgvector's build would also
  try to produce JIT files with a matching clang. JIT is optional, and skipping it avoids a large
  build dependency.
- `OPTFLAGS=""`: this stops the build from tuning the code for the build machine's CPU, so the image
  also runs on other x86-64 machines.
- The build tools are removed in the same `RUN` step, so they do not stay in the image.

The build took ~165 s on the development laptop. The container restarted on the existing volume,
and `pg_available_extensions` then listed `vector 0.8.6`.

## Trade-offs

Gain: the existing data stays valid, with no dump/restore. The Alpine base is unchanged, and the
pgvector version is pinned and visible in the repository.

Lose: a build step on first `docker compose up` (and after every change to the Dockerfile), which
needs network access to GitHub. We are also now responsible for updating pgvector ourselves.

## Future Trigger

- **A fresh environment with no data to keep** (a new deployment, CI): the official
  `pgvector/pgvector` image is simpler there, as long as all environments use the same C library.
- **A managed PostgreSQL** (a cloud service): it usually offers pgvector as a built-in extension, and
  this Dockerfile stays for local development only.
