# Version

**v14-containers**: every service gets its own Docker image, and the whole platform starts with one command.

## Problem

After v13 the platform was four Python processes plus PostgreSQL, started by hand in four terminals:

```
uvicorn app.embedding_service.main:app --port 8002
uvicorn app.model_service.main:app --port 8001
uvicorn app.main:app --port 8000
python -m app.worker
```

Picture Ahmed, a new developer who joins on Monday.

- He has to install Python 3.12 and every library, download two models, start the processes in the right order, and remember the settings.
- If the worker crashes during the night, uploads wait in the queue until someone notices. Nothing restarts it.

The repository's `Dockerfile` was still the v0 one. It ran only the API, it did not include the migrations, and it would have bundled llama.cpp and fastembed into an API that no longer uses them.

## Current Architecture

There are five processes and three ways to talk:

- HTTP between the API or the worker and the two model services
- SQL to PostgreSQL
- the PostgreSQL queue between the API and the worker (v7)

Only PostgreSQL ran in Docker.

## Why the Old Design Is Not Enough

- **Nothing restarts a crashed process** (debt (hhh) from v12).
- **Start order is manual.** The API started before the embedding service gives 503 on search until it is up, and the worker started before the migration fails.
- **"Works on my machine."** Everything depends on what is installed on this laptop.
- **There is nothing we could hand to Kubernetes or to a server.** v16 and v18 need images.
- **The API's `/health` said nothing about the two model services** (debt (eee)), so a dashboard showed "ok" while answers were failing.

## Solution

**One image per service** (ADR-034):

| Image | Dockerfile | Contains |
|---|---|---|
| `support-api` | `docker/api/Dockerfile` | the API code, migrations and the trained ticket classifier. No model libraries. Also runs the worker and the migration. |
| `support-embedding` | `docker/embedding-service/Dockerfile` | fastembed, the embedding model (downloaded at build time), only `app/core` and `app/embedding_service` |
| `support-model` | `docker/model-service/Dockerfile` | llama.cpp, Qwen2.5-1.5B (downloaded at build time, with retries), only `app/core` and `app/model_service` |
| `ai-support-db` | `docker/postgres/Dockerfile` | PostgreSQL 17 and pgvector (unchanged since v9) |

- Each image installs only the libraries listed in `docker/<service>/requirements.txt`.
- The root `requirements.txt` is used as a constraints file (`pip install -c`), so the versions stay exactly the ones we tested.
- Every image runs as a normal user, not root.

**`docker-compose.yml` starts everything in order:**

```
db (healthy) ──► migrate (alembic upgrade head, exits 0)
                    │
embedding-service (healthy) ─┬─► api (healthy)
model-service (healthy) ─────┘   worker
```

- Containers find each other by name: `http://model-service:8001`, `db:5432`.
- `restart: unless-stopped` restarts a service that crashes.
- **Only the API (8000) and the database (5432, for development) are published.** The model services cannot be reached from outside Docker.

**The API's `/health` now reports both model services**, as `"model_service": "ok" | "degraded" | "down"` and the same for `"embedding_service"`. They are **not** part of the API's own `status`. Without the answer model the API still logs people in, stores tickets and runs keyword search. In Kubernetes, if the API's health followed the model service, a restarting model service would take every API copy out of service with it.

**The signing key check moved.** The model services share the settings class but never use tokens, so `JWT_SECRET_KEY` is no longer required by `Settings`. Instead `create_app()` refuses to start the API when the key is missing or shorter than 32 characters.

## Why This Solution?

- **Docker Compose** is the simplest tool that starts several containers in order, with health checks and restarts. It needs nothing beyond Docker Desktop.
- **The models are built into the images,** so a pulled image runs anywhere with no extra step. That matters for Kubernetes (v16) and AWS (v18).
- **Simpler alternative:** one image for everything. Every container would then carry about 1.5 GB it never uses, and a change to the API would mean rebuilding the model layers.

## New Trade-offs

- **The model image is big.** It is 4.9 GB as first built; the size is explained under Measurements. The download step failed once halfway through, so it now retries and comes first in the Dockerfile.
- **Images are built on one laptop by hand.** That is fixed by CI in v15.
- **Logs are now spread over containers.** Use `docker compose logs -f api`.
- **Debugging inside a container is one step further away** than a local process.

## What Changed

| File | Change |
|---|---|
| `docker/api/Dockerfile`, `docker/api/requirements.txt` | new |
| `docker/embedding-service/Dockerfile`, `requirements.txt` | new |
| `docker/model-service/Dockerfile`, `requirements.txt` | new |
| `Dockerfile` (v0) | removed |
| `docker-compose.yml` | was only the database; now db, migrate, embedding-service, model-service, api, worker |
| `.dockerignore` | also leaves out `evaluation/` and every script except the model download |
| `app/health/router.py`, `schemas.py` | `model_service` and `embedding_service` fields |
| `app/core/config.py` | `jwt_secret_key` defaults to empty; version 0.14.0 |
| `app/main.py` | refuses to start without a key of 32 or more characters |
| `tests/test_v14_health_services.py` | new: 5 tests |
| `scripts/smoke_test.py` | new: register → upload → wait → streamed answer, against any address |

## How to Test

```bash
docker compose build
```

```bash
docker compose up -d
```

```bash
docker compose ps
```

```bash
python scripts/smoke_test.py http://localhost:8000
```

```bash
pytest
```

`JWT_SECRET_KEY` must be in your `.env`; Compose reads it from there.

To check that a crashed service comes back:

```bash
docker compose exec api python -c "import os, signal; os.kill(1, signal.SIGTERM)"
```

```bash
docker compose ps api
```

## Expected Result

- Every container shows `Up`, and the four that have health checks show `(healthy)`. `migrate` shows `Exited (0)`.
- `GET /health` returns `{"status": "ok", ..., "model_service": "ok", "embedding_service": "ok"}`.
- The smoke test prints each step and a streamed answer.
- After the crash command, `docker inspect ai-support-platform-api-1 --format "{{.RestartCount}}"` prints `1`, and the API is healthy again a few seconds later.

## Measurements

Measured locally on the CPU laptop, with Docker Desktop given 4 GB. The smoke test ran through the nginx container that v17 adds, which was already built; nginx adds milliseconds, not seconds.

| | Result |
|---|---|
| Full test suite | **258 passed**, 330 s |
| `docker compose up` to everything healthy | about 1 minute (the model loads in the model-service container) |
| Smoke test: upload → ready | 3.57 s |
| Smoke test: first answer after start | 44.8 s (first words at 44.3 s, a cold model) |
| Smoke test: second answer | 14.5 s (first words at 13.9 s) |
| The same answer outside Docker (v11/v12) | ~6–7 s |
| Port 8001, 8002 or 6379 from the host | not reachable (checked) |
| API crash (SIGTERM to PID 1) | restarted by Docker, `RestartCount=1` |

Memory per container (`docker stats`, Linux, the same moment):

| Container | Memory |
|---|---|
| model-service | 1.32 GiB |
| embedding-service | 236 MiB |
| api | 160 MiB |
| worker | 73 MiB |
| db | 44 MiB |

Image sizes: `support-api` 683 MB, `support-embedding` 688 MB, `support-model` 4.88 GB.

Things to be honest about:

- **The model image size.** The first build ran `chown -R` over `/app`, which copied the 1.1 GB model into a second layer. That step is removed in the Dockerfile. The rebuild itself was stopped twice because the laptop ran out of memory, so the 4.88 GB in the table is the old build.
- **Answers are 2–3 times slower in Docker on this laptop** than as a Windows process: 14.5 s against 6–7 s. The model service runs inside Docker Desktop's Linux VM, which here has 4 GB and shares the CPU with Windows. I have not separated the causes (CPU limits, the VM, or a cold cache).
- **Memory is much lower than in v13** (API 160 MiB here against 852 MB there). These are different measures: Linux memory in use inside a container, against Windows private bytes. They cannot be compared directly.

## Interview Explanation

> "After splitting the models out, I had five processes to start in the right order by hand, and nothing restarted one that crashed. I gave each service its own image with only the libraries it needs. The API image has no ML runtime at all, and each model is baked into its service's image, so a pulled image runs anywhere. Compose starts them in dependency order using health checks, runs the migration as a one-off container first, and restarts anything that dies; I tested that by killing the API. Only the API is published; the model services are reachable only on the internal network. The API's health endpoint now reports both model services, but they don't change its own status, so one service restarting doesn't make the whole API look dead."

## Next Possible Limitation

- **Images are built by hand on one laptop.** Nothing stops an untested commit from becoming an image, and a server cannot pull them → **v15: CI and Docker Hub**.
- **Compose is one machine.** It cannot run 3 copies of the API behind one name, and it has no rolling updates → v16.
- **Answers are slower inside Docker on this laptop** (14.5 s against 6–7 s); not yet explained.
