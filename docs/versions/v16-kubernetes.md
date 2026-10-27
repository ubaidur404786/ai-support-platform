# Version

**v16-kubernetes**: the same containers, run by Kubernetes. The API runs as 3 copies, and the rate limits are shared through Redis.

## Problem

Docker Compose (v14) starts everything on one machine, but it cannot do what an always-on service needs.

Picture a Monday morning with 30 support agents, and one API process crashes or is being updated:

- With Compose there is one API container. While it restarts, everybody gets an error.
- To run three, we would need three names, three ports and something in front to share the traffic. Compose does none of that.
- A new version means stopping the old container before the new one is ready.

The memory work in v12 and v13 (models out of the API) was done exactly so that the API could run as several small copies. Nothing ran them yet.

## Current Architecture

At the end of v14, Docker Compose on one machine runs:

- db
- migrate
- embedding-service
- model-service
- api ×1
- worker

## Why the Old Design Is Not Enough

- **No copies.** One API container is a single point of failure.
- **No routing to "ready" copies.** There is nothing that sends traffic only to copies that are able to answer.
- **No rolling updates.** A new version means downtime.
- **The rate limit would break the moment we add copies.** The v5 token bucket lives in each process's memory. We measured it in v5 (limit 5, 2 processes, 8 requests got through) and called it the trigger for Redis.

## Solution

**Kubernetes files in `k8s/`, applied with `kubectl apply -k k8s`** (ADR-037):

| Object | File | Notes |
|---|---|---|
| `api` Deployment, **3 replicas**, Service `api:8000` | `api.yaml` | readiness and liveness probes on `/health` |
| `worker` Deployment, 1 replica | `api.yaml` | same image, `python -m app.worker` |
| `model-service`, 1 replica, Service `:8001` | `model-services.yaml` | ready only when `model_loaded` is true; more copies = more model copies (v12) |
| `embedding-service`, 1 replica, Service `:8002` | `model-services.yaml` | stateless, so it could be scaled |
| `db` StatefulSet with a 2 GB disk, Service `db:5432` | `db.yaml` | one copy only: two databases on one disk would corrupt it |
| `redis` Deployment, Service `redis:6379` | `redis.yaml` | rate-limit counters, no disk |
| `migrate` Job | `migrate.yaml` | the schema changes once, not three times |
| `app-settings` ConfigMap, `app-secrets` Secret | `config.yaml`, `secret.example.yaml` | the Secret is created with `kubectl create secret`, never committed |

- Every Service is `ClusterIP`, which means it can only be reached from inside the cluster.
- During this version I reached the API by port-forwarding (`kubectl port-forward svc/api 8000:8000`). The measurements below used the web app's NodePort from v17, whose image already existed.

**Shared rate limits** (ADR-038), in `app/core/rate_limit.py`:

- `RedisRateLimiter` keeps one counter per caller per minute in Redis, using `INCR` and `EXPIRE 60`.
- `create_app` uses it whenever `REDIS_URL` is set. Without that setting, the v5 in-memory bucket is still used, so tests and single-process runs need no Redis.
- If Redis is down, requests are allowed and a warning is logged. The limit must not be what takes the API down.

**The cluster on the laptop is k3s, running inside one Docker container.** Docker Desktop's built-in Kubernetes was switched on, but it never answered on this 8 GB laptop (`Unable to connect to the server: EOF`). k3s was ready in under 40 seconds. It is the same Kubernetes distribution we will run on AWS in v18. The steps are in `k8s/README.md`.

## Why This Solution?

- **Kubernetes** is the standard tool for running copies of containers, sending traffic only to ready copies, replacing crashed copies and rolling out updates.
- **Plain YAML plus Kustomize** (built into kubectl) means every object is in a readable file, with no template language on top.
- **Redis with a fixed one-minute counter** is about 20 lines of code: two commands, and both are atomic.
- **Simpler alternatives:**
  - Compose with `--scale api=3` behind our own nginx: no readiness-based routing, and no rolling updates.
  - Sticky sessions instead of Redis: counters are lost whenever a copy restarts.

## New Trade-offs

- **A lot more YAML,** and a cluster that uses memory itself. k3s and every pod together used **2.26 GiB** of the Docker VM's 3.8 GiB.
- **The database in the cluster** is fine for a demo, but it is not how production keeps data (that would be a managed database).
- **The fixed one-minute window** allows a burst of up to twice the limit across a minute boundary.
- **Fail open:** while Redis is down, logins have no limit.
- **Images must be copied into the cluster by hand on the laptop** (`docker save | ctr images import`) until they are on Docker Hub. That step is v15's push, which is still waiting for a token with write access.

## What Changed

| File | Change |
|---|---|
| `k8s/*.yaml`, `k8s/kustomization.yaml` | new (web.yaml comes with v17) |
| `k8s/README.md` | new: how to run k3s in Docker, import images and deploy |
| `app/core/rate_limit.py` | `RedisRateLimiter` |
| `app/main.py` | uses it when `REDIS_URL` is set |
| `app/core/config.py` | `redis_url`; version 0.16.0 |
| `requirements.in`, `requirements.txt`, `docker/api/requirements.txt` | `redis` (8.1.0) |
| `docker-compose.yml` | a `redis` service and `REDIS_URL`, so Compose matches the cluster |
| `tests/test_v16_redis_rate_limit.py` | new: 6 tests with a fake Redis, including "2 copies, limit 5, 8 requests → 5 allowed" |
| `scripts/measure_shared_rate_limit.py` | new: 30 wrong-password logins, counts 401 against 429 |

## How to Test

```bash
pytest tests/test_v16_redis_rate_limit.py -v
```

Follow `k8s/README.md` to start k3s, import the images, create the Secret and apply. Then:

```bash
kubectl get pods
```

```bash
python scripts/smoke_test.py http://localhost:30080/api
```

```bash
python scripts/measure_shared_rate_limit.py http://localhost:30080/api
```

## Expected Result

- 10 pods `Running`, 3 of them `api-...`, and `migrate` `Completed`.
- The smoke test ends with a streamed answer.
- The rate-limit script prints `let through (not 429): 10` out of 30.

## Measurements

Measured locally on k3s v1.31.4 in Docker Desktop (4 GB VM), on the CPU laptop.

**The trigger, measured on 3 API copies.** Login limit 10 per minute per address, 30 wrong-password attempts sent as fast as possible:

| Rate-limit counters | Let through | Refused (429) |
|---|---|---|
| in each copy's memory (`REDIS_URL` empty) | **30** | 0 |
| in Redis (as deployed) | **10** | 20 |

With the counters in memory, 3 copies allowed 3 times the limit, which is exactly the v5 prediction. With Redis, the limit is the configured one.

**Losing a copy.** I deleted one API pod and called `/health` 40 times over 10 seconds: **40 of 40 succeeded**. Kubernetes stopped routing to the dying pod and started a replacement.

**End to end** (`scripts/smoke_test.py` through port 30080):

| Step | Time |
|---|---|
| `/health` | 0.14 s, all services `ok` |
| upload → ready | 2.38 s |
| streamed answer | 42.8 s (first words at 41.9 s, a cold model just after start) |

**Start-up:**
- From `kubectl apply` to every pod ready took about 3–4 minutes.
- The API pods restarted once each while the database was still starting. That is expected: the API starts before the migration has finished, and the worker logged "relation does not exist" until then.

**Memory:** the k3s container with everything in it used 2.26 GiB.

Things to be honest about:

- **The answer took 42 s**, because it was the first answer of a cold model, and on this laptop the model runs inside Docker's VM (see v14). I didn't measure a warm answer in the cluster.
- **The images were copied in by hand, not pulled from Docker Hub.** The Docker Hub token is read-only (`insufficient scopes`), so v15's push has not happened yet.

## Interview Explanation

> "With Compose I had one API container, a single point of failure. I moved to Kubernetes so the API runs as three replicas behind one Service, with readiness probes deciding who gets traffic. The answer model stays at one replica because every copy would load its own model and queue. That immediately broke rate limiting: each copy counted on its own. I measured it, and 30 login attempts got through a limit of 10. So I moved the counters to Redis with an atomic increment per caller per minute, which brought it back to exactly 10. If Redis is down it fails open, because the limiter shouldn't be what takes the API down. I tested resilience by deleting an API pod during traffic: 40 of 40 health checks still succeeded."

## Next Possible Limitation

- **Nobody can use the product without curl or kubectl.** There is no user interface → **v17-react-frontend**.
- **The images are not in a registry yet** (v15's push needs a token with write access), so a fresh machine cannot run this.
- **The API's start-up depends on the migration finishing,** which happens by restarting until it works. It is fine, but noisy.
- **Answers are slow inside the Docker VM** on this laptop.
