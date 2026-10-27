# AI Support Platform

A support assistant that answers customer questions from your own help articles. It runs fully on free, local models, with no paid AI APIs.

## The problem

Support teams answer the same questions every day: "How do I get a refund?", "How do I reset my password?". The answers are already written down in help articles, but someone still has to find the right article and write a reply.

## The solution

You upload your help articles. When a question comes in, the system:

1. finds the parts of your articles that match the question's **meaning** (not just the same words),
2. lets a small local language model write a short answer **only from those parts**,
3. shows the answer with its **sources**, or says "I could not find an answer" instead of making one up.

It is built as separate services (web app, API, worker, two model services), each in its own Docker container, deployed with Kubernetes.

## How to use it

1. **Create an account.** Open the web app and create an organisation and a user.
2. **Upload a help article** (`.txt`, `.md` or `.pdf`). Its status changes from `queued` to `ready` in a few seconds.
3. **Ask a question.** The answer appears word by word, with the sources it used.

**1. Log in.** The line at the bottom shows that every part of the system is running.

![Log in](docs/images/1-login.png)

**2. Upload a help article** from the "Knowledge base" box.

![Upload a document](docs/images/2-upload.png)

**3. Ask a question.** The answer is written only from your articles, and the source is shown below it.

![Ask a question](docs/images/3-answer.png)

Example (with `evaluation/knowledge_base/refund-policy.md` uploaded):

| You ask | You get |
|---|---|
| How long does a refund take? | "A refund usually takes five to ten business days to appear on your bank statement." Source: Refund policy |
| Do you have an office in Paris? | "I could not find an answer to that in the knowledge base." |

## Architecture

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

**How it was built:** the project started as one small file (v0) and grew one version at a time. Each version fixed one real problem the version before it had. All 18 versions, their diagrams and the reasons behind each choice are in **[docs/](docs/README.md)**.

## Run it (no code needed)

Every service is published on Docker Hub as a ready-made image: `ubaidur404786/support-api`, `support-web`, `support-model`, `support-embedding` and `support-db`. You only need [Docker Desktop](https://www.docker.com/products/docker-desktop/) and one file:

```bash
curl -O https://raw.githubusercontent.com/ubaidur404786/ai-support-platform/main/docker-compose.hub.yml
JWT_SECRET_KEY=$(openssl rand -hex 32) docker compose -f docker-compose.hub.yml up -d
```

The first start downloads the images (about 4.5 GB on disk; the two AI models are inside them), so give it a few minutes. Then open **http://localhost:8080**.

Stop it with:

```bash
docker compose -f docker-compose.hub.yml down
```

## Running on Kubernetes

The same images run on Kubernetes (k3s). The files are in [k8s/](k8s/README.md); one command starts everything:

```bash
kubectl apply -k k8s
```

![How the parts talk to each other on Kubernetes](docs/architecture/kubernetes.svg)

**What each Kubernetes object is used for:**

| Kubernetes object | Used for | Why |
|---|---|---|
| **Deployment** | api (3 pods), worker, model-service, embedding-service, redis, web | Keeps the right number of pods running and replaces any pod that dies |
| **StatefulSet** + disk | PostgreSQL (1 pod) | A database needs a fixed name and data that survives a restart |
| **Job** | database migration | Creates the tables once, before the API is used |
| **Service** (ClusterIP) | `api`, `db`, `redis`, `model-service`, `embedding-service` | A fixed internal name. A pod calls `http://model-service:8001` and Kubernetes sends it to a healthy model pod |
| **Service** (NodePort 30080) | `web` | The only way in from outside the cluster |
| **ConfigMap / Secret** | settings / passwords and keys | Settings are not baked into the images, and secrets are never in Git |
| **Probes** | every service | A pod only receives traffic once it is ready (for example, once its model is loaded) |

A request travels like this:

```
browser → web Service (NodePort 30080) → web pod (nginx)
        → api Service → one of 3 api pods
        → embedding-service / db / model-service Services → their pods
```

The API runs as **3 pods**. If one is deleted, the other two keep answering: in a test, 40 of 40 requests still succeeded. The 3 pods share their rate-limit counters through Redis; without it, they let 30 login attempts through a limit of 10. The answer model runs as **1 pod**, because every copy needs its own 1.1 GB model.

Details: [k8s/README.md](k8s/README.md) and [the v16 write-up](docs/versions/v16-kubernetes.md).

## Next step: AWS

The plan for the next version (v18) is to run the same Kubernetes files on the cloud:

1. One AWS EC2 server (8 GB of memory), with k3s installed.
2. The cluster pulls the images from Docker Hub. `k8s/aws/` only changes the image names.
3. Only port 30080 is open to the internet, so the web app is the single public entry point.
4. A recorded demo, then the server is deleted, so it costs only a few cents.

## Develop it

To change the code, clone the repository and build the images yourself:

```bash
git clone https://github.com/ubaidur404786/ai-support-platform.git
cd ai-support-platform
cp .env.example .env        # then set JWT_SECRET_KEY in .env
docker compose up -d --build
```

## Run the tests

```bash
docker compose up -d db
pip install -r requirements.txt
python ml/train.py
pytest
```

GitHub Actions runs the same tests on every push and, on `main`, publishes the images to Docker Hub.

The test files are numbered by the version that added them; see [tests/README.md](tests/README.md).

## Project layout

```
app/                  the backend (one folder per feature)
  auth/                 accounts and login
  documents/            upload, background processing, search
  answers/              question → answer
  model_service/        the answer model (its own service)
  embedding_service/    the search model (its own service)
frontend/             the React web app
docker/               one Dockerfile per service
k8s/                  Kubernetes files
tests/                tests, numbered by version
docs/                 version history, diagrams, decisions
```
