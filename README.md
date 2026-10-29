# AI Support Platform

A support assistant that answers customer questions from a company's own help articles. You upload the articles. When a customer asks something, the assistant finds the right passages, writes a short answer from them only, and shows where the answer came from. If the articles don't contain an answer, it says so instead of guessing.

It runs entirely on free, local AI models, with no paid AI service.

| Area | In short |
|---|---|
| **For a support team** | Faster, consistent answers to repeated questions, always backed by a source |
| **AI** | Search by meaning (MiniLM embeddings + pgvector) and answers from a small local language model (Qwen2.5-1.5B) |
| **Engineering** | Separate services in Docker containers, run by Kubernetes, tested and published by CI |
| **Status** | Finished and runs locally. 19 versions (v0-v18), each one fixing a measured problem |

---

## 1. What happens when more people use it?

The question every system faces: **more users arrive, so what slows down, and what should we add?** These answers come from a [load-testing lab](system-design/README.md). Every container gets 1 CPU, and every setup gets the same test.

**One server: more users just wait longer.** Throughput stays around 25 requests/s however many users arrive, so each new user only joins the queue. At 100 users the median wait is 5.6 s.

<table>
<tr>
<td width="50%"><img src="system-design/architecture/chart-tickets-throughput.svg" width="100%"></td>
<td width="50%"><img src="system-design/architecture/chart-tickets-latency.svg" width="100%"></td>
</tr>
</table>

**Why: only copying the busy part helps.**
- 3 gateways (orange line, lying on top of the blue 1-gateway line): no change, because the gateway was never the slow part.
- 3 cells, where each gateway gets its own copy of the busy service (green line): **2.2× the throughput**, and the median wait falls **from 5.6 s to 1.9 s**.

![Scaling, measured](system-design/architecture/step2-scaling.svg)

**But the busy part depends on the request.** This endpoint does almost no work in the service, so the gateway is the slow part, and adding gateways gave about **3× the throughput**.

<table>
<tr>
<td width="50%"><img src="system-design/architecture/chart-users-throughput.svg" width="100%"></td>
<td width="50%"><img src="system-design/architecture/chart-users-latency.svg" width="100%"></td>
</tr>
</table>

**And the database?** Replicas and shards on one laptop gave **no gain**, because the copies shared the same CPU and disk. Shards helped only the whole-table read, because each shard scans half the rows. Copies help only when they get hardware of their own.

<table>
<tr>
<td width="50%"><img src="system-design/architecture/chart-database.svg" width="100%"></td>
<td width="50%"><img src="system-design/architecture/step3-replication.svg" width="100%"></td>
</tr>
</table>

## 2. The design these results led to

![System design](docs/architecture/v18.svg)

| Decision | Because of |
|---|---|
| Copy the **slowest part** first (the answer model) | The tickets charts: copying the busy service doubled throughput |
| Keep the **API stateless**, in one pool | Copies only help if any copy can serve any user |
| **One read replica**, on its own machine | The database chart: copies on shared hardware gave nothing |
| **No sharding** yet | Writes were not the bottleneck (61 vs 58 req/s) |
| Say **"busy" early** | Above capacity, users only wait longer (the latency chart) |

**When something fails:**
- Kubernetes restarts a crashed copy. With one of the 3 API pods deleted, 40 of 40 requests still succeeded.
- An upload waits in the queue until the worker is back.
- If the answer model is down, the API says so instead of hanging.

## 3. How it works for a user

![How the system works for a user](docs/architecture/user-flow.svg)

## 4. Architecture

![System architecture](docs/architecture/system.svg)

## 5. Running on Kubernetes

![Kubernetes](docs/architecture/kubernetes.svg)

## 6. The web app

<table>
<tr>
<td width="33%"><b>1. Log in</b><br><img src="docs/images/1-login.png" width="100%"></td>
<td width="33%"><b>2. Upload a help article</b><br><img src="docs/images/2-upload.png" width="100%"></td>
<td width="33%"><b>3. Ask a question</b><br><img src="docs/images/3-answer.png" width="100%"></td>
</tr>
</table>

## 7. Run it

You only need [Docker Desktop](https://www.docker.com/products/docker-desktop/). The images come from Docker Hub, so you don't need to clone the repository.

```bash
curl -O https://raw.githubusercontent.com/ubaidur404786/ai-support-platform/main/docker-compose.hub.yml
JWT_SECRET_KEY=$(openssl rand -hex 32) docker compose -f docker-compose.hub.yml up -d
```

Open **http://localhost:8080**. The first start downloads about 4.5 GB, because the two models are inside the images. To stop it, run `docker compose -f docker-compose.hub.yml down`.

On Kubernetes: `kubectl apply -k k8s`.

## 8. Final remarks

- **Built by measuring, not guessing.** Every version started from a measured problem, for example "listing tickets took 2.2 s" or "the first search took 21 s", and ended with a number after the fix.
- **Scale what is slow.** The lab showed that adding copies of the wrong part costs money and changes nothing.
- **Honest limits:**
  - On a laptop CPU, an answer takes about 7 s, and one model copy handles 5-10 answers a minute.
  - Replicas and shards were tested only on one machine.
  - Wrong answers are not yet detected while the system runs.

## 9. What could come next

![Next step: the same design on a cloud](docs/architecture/cloud-next.svg)

With cloud credits or a budget for usage, the same design moves to **AWS, Azure or GCP**:
- managed Kubernetes that adds copies automatically
- GPU machines for the answer model
- a managed PostgreSQL with a read replica
- monitoring

Other next steps:
- a cache for repeated questions
- checking answers for mistakes while the system runs
- a monitoring dashboard
- a safe way to roll out a new model and compare it with the old one

## 10. The full story

How the project started as one small file, what went wrong at each step, and every decision with its numbers:

**[docs/ - the journey, version by version](docs/README.md)**

---

### Develop and test

```bash
git clone https://github.com/ubaidur404786/ai-support-platform.git
cd ai-support-platform
cp .env.example .env        # then set JWT_SECRET_KEY in .env
docker compose up -d --build
pip install -r requirements.txt && python ml/train.py && pytest
```

GitHub Actions runs the tests on every push. The tests are numbered by the version that added them ([tests/README.md](tests/README.md)).

```
app/             backend, one folder per feature (auth, documents, answers, model_service, embedding_service)
frontend/        React web app
docker/          one Dockerfile per service
k8s/             Kubernetes files
system-design/   the scaling lab: nginx, gateways, replicas, shards, load tests, charts
tests/           tests, numbered by version
docs/            the journey: versions, diagrams, decisions
```
