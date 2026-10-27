# Version

**v13-embedding-service**: the embedding model moves out of the API and the worker into its own service.

## Problem

Two processes needed the embedding model, and each one loaded its own copy:

- the **API**, to turn each search question into a vector
- the **worker**, to turn each chunk of an uploaded document into a vector

Picture Maria, a support agent, coming in on Monday after the API was restarted over the weekend. Her first semantic search takes **20.9 seconds**, because the API is loading the model in the middle of her request. Her second search takes 0.4 s.

Every extra API copy we start in Kubernetes later (v16) would pay this cost again: another copy of the model in memory, and another slow first search.

## Current Architecture

At the end of v12 we ran three processes:

- **API**: auth, tickets, documents, search and answers. It loaded the embedding model on the first search.
- **Worker**: processes queued uploads. It loaded the embedding model when it started.
- **Model service**: runs the answer model, with one queue (v12).

## Why the Old Design Is Not Enough

Measured on the development laptop (CPU only, 8 GB RAM), with v12 code:

| | v12 |
|---|---|
| API process, private memory | 975 MB |
| Worker process, private memory | 556 MB |
| First semantic search after the API starts | **20,862 ms** |
| Next 20 searches, P50 | 386 ms |

The model is the same in both processes, so the second copy only costs memory. The slow first search is a direct cost to a user.

## Solution

A fourth process, the **embedding service** (`app/embedding_service/`):

```
API    ─┐                                   ┌─ GET  /health
        ├── HTTP :8002 ──► embedding service ├─ POST /embed  {"texts": [...], "model": "..."}
Worker ─┘                                   └─   → {"vectors": [[0.01, ...], ...]}
```

- `app/embedding_service/model.py` loads fastembed. It is the **only** file in `app/` that mentions fastembed, and a test checks this.
- `app/embedding_service/main.py` loads the model when the service starts, not on the first request.
  - It refuses a request that asks for a different model with `400`. Vectors from two models cannot be compared.
  - It refuses more than 64 texts in one request.
- `app/documents/embeddings.py` keeps the same `embed_texts()` function, so nothing that calls it had to change. Inside, it now:
  - sends the texts over HTTP, splitting long lists into requests of 64
  - turns "down" or "error" into `EmbeddingUnavailable`, which already became a 503 in v8

## Why This Solution?

- It is the same pattern as v12's model service. There is nothing new to learn, and the two services look alike.
- The API and the worker no longer need fastembed and ONNX Runtime. In v14 their Docker image leaves them out entirely.
- **Simpler alternative:** load the model when the API starts, not on the first request. That fixes the slow first search but keeps two copies, and two more for every extra API copy.

## New Trade-offs

- **There is one more process that can be down.** We checked what happens with the service stopped:
  - semantic search returns 503
  - keyword search still returns 200
  - new uploads wait in the queue and are retried
- **Each embedding costs a network round trip.** On this laptop that is a few milliseconds next to tens of milliseconds of model time.
- **The total memory is not smaller with one API process.** It only becomes smaller once there are several API copies. See Measurements.

## What Changed

| File | Change |
|---|---|
| `app/embedding_service/main.py` | new: the service (`/health`, `/embed`) |
| `app/embedding_service/model.py` | new: fastembed, moved from `app/documents/embeddings.py` |
| `app/documents/embeddings.py` | now an httpx client; same `embed_texts()` |
| `app/core/config.py` | `embedding_service_url`, `embedding_service_timeout_seconds`; version 0.13.0 |
| `app/worker.py` | the start-up check now checks the service |
| `tests/conftest.py` | `embedding_service` fixture: the real service in memory for every test |
| `tests/test_v13_embedding_service.py` | new: 9 tests |
| `scripts/measure_embedding_service.py` | new: first search, next 20 searches, upload to ready |
| `.env.example` | the two new settings |

## How to Test

```bash
pytest tests/test_v13_embedding_service.py -v
```

```bash
pytest
```

Run it by hand. You need four terminals, with `DB_ECHO=false`:

```bash
uvicorn app.embedding_service.main:app --port 8002
```

```bash
uvicorn app.model_service.main:app --port 8001
```

```bash
uvicorn app.main:app --port 8000
```

```bash
python -m app.worker
```

```bash
curl http://127.0.0.1:8002/health
```

```bash
python scripts/measure_embedding_service.py
```

## Expected Result

- `/health` on port 8002 returns `{"status": "ok", "model_loaded": true, "model": "sentence-transformers/all-MiniLM-L6-v2"}`.
- The first semantic search after the API starts takes well under a second.
- If you stop the embedding service, `GET /documents/search?mode=semantic` returns 503 and `GET /documents/search` (keyword search) still returns 200.

## Measurements

Measured locally on the same laptop, in the same session, with `RATE_LIMIT_ENABLED=false` and `DB_ECHO=false`:

| | v12 (model in API + worker) | v13 (embedding service) |
|---|---|---|
| First semantic search after API start | 20,862 ms | 377 ms (209 ms on a second run) |
| Next 20 searches, P50 | 386 ms | 252 ms (202 ms) |
| API process | 975 MB | 852 MB |
| Worker process | 556 MB | 421 MB |
| Embedding service | – | 530 MB |
| **Total of these processes** | **1,531 MB** | **1,803 MB** |
| Service start-up (model loaded) | – | 5 s |

Being honest about the memory:

- With **one** API process, the total went **up** by 272 MB. The service has its own Python, FastAPI and NumPy, which cost more than the copy of the model we removed.
- Each API or worker process is about 125 to 135 MB smaller, so the change pays back from the third API copy. That happens in v16, when Kubernetes runs 3 copies of the API.
- The search P50 also went down, but this laptop is noisy, so I don't claim it as a result.

Upload to ready took 3.1 s in v13 against 7.5 s in v12. That was one document each time, so it is too few to call a result.

The full test suite has 247 tests. One failed on the first run because a log message changed; I fixed the test and that test file passed again (17 tests).

## Interview Explanation

> "Search questions and document chunks are both turned into vectors by the same small model, and two processes loaded their own copy. The first search after a restart took 21 seconds because the API loaded the model inside that request. I moved the model into a small embedding service, the same pattern I had used for the answer model. Both callers use HTTP, the model loads once when the service starts, and the first search dropped to under 0.4 s. With one API process, total memory actually went up by about 270 MB, because the new process has its own runtime. The win only shows once there are several API copies, which is what Kubernetes gives us next. If the service is down, semantic search and answers return 503, while keyword search and tickets keep working."

## Next Possible Limitation

- **Four processes to start by hand, and nothing restarts them.** The Docker image from v0 does not match the code any more → **v14-containers**.
- The API's `/health` does not tell you whether the model service or the embedding service is up.
- Both services trust any caller on the network. That is fine on one machine, but in v14 they must not be reachable from outside.
