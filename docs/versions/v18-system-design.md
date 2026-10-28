# Version v18: system design lab

## Problem

Before paying for cloud machines, the project needs a clear answer: **when users grow, which part must be copied, and why?** So far, scaling was done only once, in v16 (3 API pods), and it was never compared with other options.

## Current Architecture

v17: web (nginx) → API ×3 → embedding service, model service, PostgreSQL and Redis, all on k3s. An answer takes ~7 s on one CPU.

## Why the Old Design Is Not Enough

Experiments on the real platform are slow (seconds per answer) and need about 4 GB of memory. That makes it hard to try ten architectures in an afternoon. The platform also mixes several bottlenecks at once.

## Solution

A small, separate lab in `system-design/`:

- nginx with a hand-written `nginx.conf`, mounted as a volume
- 1 or 3 gateways
- users and tickets services
- PostgreSQL, optionally with read replicas or shards
- a load test that spreads its users over several processes
- one script that runs every setup, and one that draws the charts

Each step adds one compose file on top of the base file.

## Why This Solution?

- It is the smallest system that still has a load balancer, gateways, services and a database.
- A fixed amount of CPU work stands in for the model, so results repeat.
- Each container is limited to 1 CPU, so one copy behaves like one small server.

## New Trade-offs

- A second, separate codebase to keep working.
- Results on one laptop understate what replicas and shards can do, because the copies share the same hardware.

## What Changed

`system-design/` is new. It contains:

- the gateway and services
- the nginx configs
- `db/init.sh`, `db/replica.sh` and `db/schema.sql`
- `step2-gateways.yml`, `step2-cells.yml`, `step3-replicas.yml` and `step4-shards.yml`
- `loadtest/loadtest.py`, `run_experiments.sh` and `plot_results.py`
- `architecture/*.svg` and `results/`

`docs/architecture/v18.svg` is also new. It shows the system design for the AI platform.

## How to Test

```bash
cd system-design
docker compose up -d --build
curl -i http://localhost:8090/api/tickets/7
sh run_experiments.sh
python plot_results.py
```

## Expected Result

For each setup, the load test prints one line per user level with:

- requests per second
- P50, P95 and P99 latency
- errors
- how requests were split over the gateways

`plot_results.py` then redraws `architecture/chart-*.svg`.

## Measurements

Conditions: a laptop with Docker Desktop, 1 CPU per container, 10 s per level, all in one session.

| Setup | GET /api/tickets at 100 users | GET /api/users at 100 users |
|---|---|---|
| 1 gateway | 25.5 req/s, P50 5,568 ms | 51 req/s, P50 1,422 ms |
| 3 gateways, 1 copy of each service | 26.6 req/s, P50 5,604 ms | 166 req/s, P50 430 ms |
| 3 cells (gateway + own services) | 55.9 req/s, P50 1,901 ms | 203 req/s, P50 188 ms |

| Database setup, 50 users | Stats (whole-table read) | Writes |
|---|---|---|
| 1 database | 13.1 req/s | 58.1 req/s |
| primary + 2 replicas | 12.7 req/s | - |
| 2 shards | 17.7 req/s | 61.1 req/s |

What `docker stats` showed for where the time went:

- **Step 1, tickets path:** tickets-service ran at 93% CPU.
- **Step 3:** both replicas ran at 100% CPU. One stats query took the same 160-170 ms on a replica as on the primary.
- **Step 4, writes:** with shards, each database used only 25-30% CPU.

**Not separated:**

- Why replicas gave no gain. The likely cause is that all databases share one laptop's cores, memory and disk.
- What limits writes with shards: the shared disk or the Python services.

## Interview Explanation

I built a small lab with nginx, gateways, services and PostgreSQL, gave every container one CPU, and measured each change.

- **Busy service:** on an endpoint where the service was the busy part, adding gateways did nothing. Adding service copies doubled throughput and cut median latency from 5.6 s to 1.9 s.
- **Busy gateway:** on an endpoint where the gateway was the busy part, more gateways tripled throughput. So you find the bottleneck first, then copy that.
- **Replicas and shards:** no gain on one laptop, because copies on one machine add no hardware.

For the AI platform, that means:

- Scale the model service first.
- Keep the API stateless behind a load balancer.
- Add one read replica on separate hardware.
- Do not shard. If sharding is ever needed, the key is `organization_id`.

## Next Possible Limitation

Everything still runs on one laptop. v19 moves to AWS: separate machines, a real load balancer, and the replica measurement repeated where each replica has its own hardware.
