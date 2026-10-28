# AI Support Platform

A support assistant that answers customer questions from your own help articles. It finds the passages that match the question's meaning, and a small local language model writes an answer only from those passages and shows its sources. If the articles don't contain the answer, it says "I could not find an answer" instead of making one up. It runs entirely on free, local models.

## System design

![Platform system design](docs/architecture/v18.svg)

**Why this design:** each choice was measured in the [system design lab](system-design/README.md).

![Scaling results](system-design/architecture/step2-scaling.svg)

Details: [docs/system-design-details.md](docs/system-design-details.md) · [ADR-040](docs/adr/ADR-040-scale-the-bottleneck.md)

## The web app

**1. Log in.** The line at the bottom shows that every part is running.

![Log in](docs/images/1-login.png)

**2. Upload a help article** (`.txt`, `.md` or `.pdf`). Its status changes from `queued` to `ready` in a few seconds.

![Upload a document](docs/images/2-upload.png)

**3. Ask a question.** The answer appears word by word, with the source it came from.

![Ask a question](docs/images/3-answer.png)

## What runs today

![System architecture](docs/architecture/system.svg)

| Service | Job | Technology |
|---|---|---|
| Web app | the pages; the only part open to the internet | React, nginx |
| API (×3) | login, documents, search, answers | FastAPI |
| Worker | reads uploaded files in the background | Python |
| Embedding service | turns text into numbers that describe its meaning | all-MiniLM-L6-v2 |
| Model service | writes the answer | Qwen2.5-1.5B with llama.cpp (CPU) |
| PostgreSQL | all data, plus vector search | PostgreSQL 17, pgvector |
| Redis | rate limits shared by the API copies | Redis |

**On Kubernetes** (k3s, files in [k8s/](k8s/README.md)), each service is a Deployment with a fixed internal name (a Service). Only `web` is open, on NodePort 30080. When one of the 3 API pods was deleted, 40 of 40 requests still succeeded.

![Kubernetes](docs/architecture/kubernetes.svg)

The project grew one version at a time, and each version fixed one measured problem. All 19 versions (v0 to v18) are in **[docs/](docs/README.md)**.

## Run it (no code needed)

Every service is published on Docker Hub. You only need [Docker Desktop](https://www.docker.com/products/docker-desktop/) and one file:

```bash
curl -O https://raw.githubusercontent.com/ubaidur404786/ai-support-platform/main/docker-compose.hub.yml
JWT_SECRET_KEY=$(openssl rand -hex 32) docker compose -f docker-compose.hub.yml up -d
```

The first start downloads about 4.5 GB, because the two AI models are inside the images. Then open **http://localhost:8080**. To stop it, run `docker compose -f docker-compose.hub.yml down`.

On Kubernetes: `kubectl apply -k k8s`.

## Next step: AWS (v19)

The same design, on separate machines:
- an EC2 server with k3s
- images pulled from Docker Hub
- only the web port open

The read replica will be measured again there, because on AWS it gets its own CPU and disk.

## Develop and test

```bash
git clone https://github.com/ubaidur404786/ai-support-platform.git
cd ai-support-platform
cp .env.example .env        # then set JWT_SECRET_KEY in .env
docker compose up -d --build
pip install -r requirements.txt && python ml/train.py && pytest
```

GitHub Actions runs the tests on every push. On `main`, it also publishes the images to Docker Hub. The test files are numbered by the version that added them ([tests/README.md](tests/README.md)).

## Project layout

```
app/                  the backend (one folder per feature)
frontend/             the React web app
docker/               one Dockerfile per service
k8s/                  Kubernetes files
system-design/        the lab: nginx, gateways, replicas, shards, load tests, charts
tests/                tests, numbered by version
docs/                 version history, diagrams, decisions
```
