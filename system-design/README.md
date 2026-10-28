# v18 System design lab

A small, separate system for learning **how and why an architecture changes when the number of users grows**. It has no AI model, so each experiment takes seconds. A fixed amount of CPU work in `tickets-service` stands in for the model.

It was built step by step. Each step changes one thing and is measured.

| Step | Change | Result at 100 users (GET /api/tickets) |
|---|---|---|
| 1 | One gateway | 26 req/s, P50 5.6 s |
| 2a | Three gateways, one copy of each service | 27 req/s, P50 5.6 s (no gain) |
| 2b | Three gateways, each with its own services (a "cell") | **56 req/s, P50 1.9 s** |
| 3 | Two read replicas of the database | no gain on one laptop (see why) |
| 4 | Two shards (tickets split by user) | writes no gain; whole-table reads +35% |

The design chosen for the AI support platform, and why, is at the end: [From the lab to the platform](#from-the-lab-to-the-platform).

## All diagrams, in order

Read left to right, top to bottom. Each section below explains one of them.

<table>
<tr>
<td width="50%"><b>1. The complete picture</b><br><img src="architecture/overview.svg" width="100%"></td>
<td width="50%"><b>2. How the containers find each other</b><br><img src="architecture/networking.svg" width="100%"></td>
</tr>
<tr>
<td colspan="2"><b>3. Steps 1 and 2: one gateway → three gateways → three cells (measured)</b><br><img src="architecture/step2-scaling.svg" width="100%"></td>
</tr>
<tr>
<td><b>4. Throughput, tickets (service-bound)</b><br><img src="architecture/chart-tickets-throughput.svg" width="100%"></td>
<td><b>5. Latency, tickets</b><br><img src="architecture/chart-tickets-latency.svg" width="100%"></td>
</tr>
<tr>
<td><b>6. Throughput, users (gateway-bound)</b><br><img src="architecture/chart-users-throughput.svg" width="100%"></td>
<td><b>7. Latency, users</b><br><img src="architecture/chart-users-latency.svg" width="100%"></td>
</tr>
<tr>
<td><b>8. Step 3: read replicas</b><br><img src="architecture/step3-replication.svg" width="100%"></td>
<td><b>9. Step 4: sharding</b><br><img src="architecture/step4-sharding.svg" width="100%"></td>
</tr>
<tr>
<td><b>10. Database steps compared</b><br><img src="architecture/chart-database.svg" width="100%"></td>
<td><b>11. The design chosen for the platform</b><br><img src="../docs/architecture/v18.svg" width="100%"></td>
</tr>
</table>

## The parts

| Container | File | Job |
|---|---|---|
| nginx | `nginx/nginx.conf`, `nginx/step2.conf` | Load balancer, the only door from outside (port 8090) |
| gateway1..3 | `gateway/main.py` | Reads the path and forwards to the right service |
| users-service | `services/users/main.py` | Reads users; mostly waits on the database |
| tickets-service | `services/tickets/main.py` | Reads + classifies a ticket (~50 ms CPU), creates tickets, stats |
| db | `db/init.sh`, `db/schema.sql` | PostgreSQL: 1,000 users, 200,000 tickets |
| db-replica-1, -2 | `db/replica.sh` | Read-only copies of db (step 3) |
| db-shard-1 | `db/schema.sql` | Second half of the tickets (step 4) |
| loadtest | `loadtest/loadtest.py` | Simulated users; runs only when you call it |

Every app container is limited to **1 CPU**, like one small server.

## Run and measure

```bash
cd system-design
docker compose up -d --build                     # step 1
curl -i http://localhost:8090/api/tickets/7      # see X-Gateway and served_by
docker compose run --rm loadtest python loadtest/loadtest.py --path /api/tickets --users 1,10,50,100
```

Each later step adds a file on top of `docker-compose.yml`:

```bash
docker compose -f docker-compose.yml -f step2-gateways.yml up -d                          # 2a
docker compose -f docker-compose.yml -f step2-gateways.yml -f step2-cells.yml up -d       # 2b
docker compose -f docker-compose.yml -f step3-replicas.yml up -d                          # 3
docker compose -f docker-compose.yml -f step4-shards.yml up -d                            # 4
```

Run every experiment and redraw every chart (about 20 minutes):

```bash
sh run_experiments.sh
python plot_results.py
```

In Git Bash on Windows, put `MSYS_NO_PATHCONV=1` before a load-test command. Without it, Git Bash turns `/api/tickets` into a Windows path. `run_experiments.sh` already sets it.

To see which container is busy while a test runs, use a second terminal:

```bash
docker stats --no-stream
```

Stop everything with `docker compose down -v`.

**How the numbers were taken:** on a laptop, with Docker Desktop and 12 CPUs, all in one session. Each level ran for 10 s, and every setup used the same load test. The load test spreads its users over 4 processes, so the tester itself is never the slowest part. Single points move ±30% between runs. Trust the clear gaps (2× or more), not the small ones. Raw numbers are in `results/`.

---

## Step 1: one gateway

*Diagram 3, left column.*

The flow of one request:

```
browser -> nginx:80 -> gateway1:8000 -> tickets-service:8002 -> db:5432
                    (nginx.conf)     (env var TICKETS_SERVICE_URL)   (env var DATABASE_URLS)
```

**What we saw:**
- Throughput stayed between 13 and 26 req/s, no matter how many users there were.
- Latency grew in a straight line with users: P50 was 70 ms at 1 user, 2.9 s at 50 users and 5.6 s at 100 users.
- `docker stats` showed **tickets-service at 93% CPU** and gateway1 at 13%.

**Why:** one Python process does one classification at a time, so extra users only wait in line. Wait ≈ users ÷ throughput. At 100 users that is 100 ÷ 20 = 5 s, which matches the measured 5.6 s.

---

## Step 2: horizontal scaling (adding copies)

**Horizontal scaling** means adding more copies of a part. **Vertical scaling** means giving one copy a bigger machine. Copies only help if a request can go to any copy, so the copies must keep no state of their own (here, everything is in PostgreSQL).

*Diagram 3.*

*Diagrams 4 and 5.*

**2a: three gateways, one tickets-service.** Throughput was 27 req/s instead of 26. Nothing changed, because the gateways were not the slow part. All three gateways still sent their work to the same busy tickets-service.

**2b: three cells.** Each gateway has its own users-service and tickets-service. Throughput rose to **56 req/s** (×2.2), and P50 at 100 users fell from **5.6 s to 1.9 s**. Three CPUs now classify at the same time. The gain is close to, but below, ×3, because the copies share one laptop.

**The other endpoint shows the other side.** `GET /api/users/{id}` does almost no CPU work in the service, so there the gateway itself is the busy part. In step 1 the gateway used 72% CPU. With three gateways, throughput went from **~50-70 to ~155-200 req/s**.

*Diagrams 6 and 7.*

**The rule:** first find which box is busy, then add copies of that box. Different endpoints can have different bottlenecks.

### How to scale by hand

1. **Add a gateway.** Copy a gateway block in `step2-gateways.yml` and give it a new name (`gateway4`).
2. **Tell nginx about it.** Add `server gateway4:8000;` to the `upstream gateways { ... }` block in `nginx/step2.conf`.
3. **Apply the change.** Run `docker compose ... up -d`, then `docker compose restart nginx`. nginx reads its config and looks up the names only when it starts.
4. **Remove a gateway** the same way in reverse: first take it out of the nginx config, then stop the container. This way nginx never sends traffic to a container that is gone.

**How nginx chooses.** By default nginx uses **round robin**: 1, 2, 3, 1, 2, 3. The `gateways=` count in the load test output shows an even split, for example 677 / 677 / 677. If a gateway does not answer, nginx tries the next one. `least_conn;` inside the upstream block would send each request to the gateway with the fewest open requests instead.

**How a gateway finds its services.** It reads two environment variables, `USERS_SERVICE_URL` and `TICKETS_SERVICE_URL`. Docker's DNS turns `tickets-service-2` into that container's IP address.

---

## Docker Compose networking

*Diagram 2.*

- **One private network.** Every container joins it and reaches the others by **service name**: `http://gateway1:8000`, `db:5432`. No IP addresses appear in any file.
- **Ports.** `ports: "8090:80"` means "port 8090 on my laptop leads to port 80 in this container." Only nginx has a port mapping, because only the browser needs to get in from outside. The gateways, services and databases have none, so nobody outside can call them directly.
- **nginx config is a volume.** `./nginx/nginx.conf:/etc/nginx/nginx.conf:ro` puts the file from your disk into the container, read-only. To change it, edit the file and restart nginx; there is nothing to rebuild. Step 2 mounts `step2.conf` at the same path, which replaces the step 1 file.
- **Order of start.** `depends_on` with `condition: service_healthy` starts the services only after the database answers `pg_isready`.

---

## Step 3: database replication

*Diagrams 8 and 10.*

**What it is.** One **primary** database takes every write. **Read replicas** are full, read-only copies. Each replica copies the primary once with `pg_basebackup`, then receives every change as the primary writes it. This is called streaming replication, and the changes travel as WAL, the primary's log of changes. tickets-service sends the heavy `GET /tickets/stats` query to the replicas in turn and sends all writes to the primary.

**Measured: no gain on one laptop.** At 50 users, stats ran at **13.1 req/s with one database and 12.7 req/s with two replicas**.
- Both replicas were at 100% CPU, and the query did the same work on a replica as on the primary (about 160-170 ms each when run alone).
- So the cause is not replication. All three databases share one laptop's cores, memory and disk, so splitting the reads across them added no real hardware.
- Replication lag was too small to show in `pg_stat_replication` while 50 users were writing.

**When to use replication:**
- There are many more reads than writes, the reads are heavy, and the primary is the busy part, **on separate machines**.
- You need a standby: if the primary dies, a replica can be promoted to primary.
- **Cost:** a replica is a little behind the primary. A user who just wrote something should read it back from the primary.
- **When not to use it:** when the problem is writes. Every replica must apply every write too, so replicas cannot add write capacity.

---

## Step 4: sharding

*Diagrams 9 and 10.*

**What it is.** The tickets are split by a **shard key**, here `user_id % 2`.
- Even user ids live on `db` (shard 0) and odd user ids on `db-shard-1` (shard 1). After the test: 101,415 tickets on shard 0 and 101,427 on shard 1.
- Users are small, so every shard keeps all of them.
- The code that picks the shard is one line in `services/tickets/main.py`: `shards[user_id % len(shards)]`.

**Measured:**
- **Writes:** 58 req/s with one database, 61 req/s with two shards at 50 users. No gain: each database used only 25-30% CPU, so the database was not the limit. The limit was the shared disk and the Python services.
- **Whole-table stats:** 13.1 → 17.7 req/s. Each shard scans only half the rows, and the service adds the two answers together.
- **New cost:** `GET /tickets/7` without `?user_id=` now returns **400**, because the service cannot know which shard holds ticket 7 without asking every shard.

**When to use sharding: last.** It fits when the data or the writes no longer fit one primary, even after indexes, a bigger machine, replicas and caching.
- What you lose: queries without the shard key must ask every shard, and joins and totals across shards are done in code.
- Moving data when you add a shard is a big job.

---

## From the lab to the platform

*Diagram 11.*

The lab's results, applied to the AI support platform:

| Decision | Because the lab showed | In the platform |
|---|---|---|
| **Copy the slow part, not everything** | 3 gateways alone: 26 → 27 req/s; copying the busy service: 26 → 56 | The model service is the busy part (~7 s per answer, one CPU). Add model-service copies first. |
| **Keep the API stateless, behind one load balancer** | Copies help only when any copy can serve any request | The API already keeps no state: logins are checked from the token itself (JWT), and rate-limit counts are in Redis. nginx or a Kubernetes Service can spread requests over a shared pool of API copies. |
| **A pool, not fixed cells** | Cells were easy to see in the lab, but in a cell, if one service dies, its gateway's capacity is gone | Kubernetes Services already balance across all healthy copies (v16). |
| **One read replica, later** | Replicas add capacity only on separate machines (step 3) | Searches read many rows and answers write few. On AWS, one replica takes the search reads and is also the standby. |
| **No sharding** | Sharding a database that is not the bottleneck adds cost and no speed (step 4) | One organisation's documents fit easily on one database. If it is ever needed, the shard key is `organization_id`, because every query already filters by it. |
| **Say "busy" early** | Above capacity, users only wait longer (step 1: P50 grew from 70 ms to 5.6 s) | A bounded queue on the model service answers "busy" at once instead of making users wait minutes. |

**Next: v19, AWS.** The same parts run on separate machines. There, a replica gets its own CPU and disk, so step 3 should be measured again.
