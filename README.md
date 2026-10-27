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

## Run it

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
git clone https://github.com/ubaidur404786/ai-support-platform.git
cd ai-support-platform
cp .env.example .env        # then set JWT_SECRET_KEY in .env
docker compose up -d --build
```

The first build downloads the two models (about 20 minutes). Then open **http://localhost:8080**.

Stop it with:

```bash
docker compose down
```

**On Kubernetes:** see [k8s/README.md](k8s/README.md). The app is then at http://localhost:30080.

## Run the tests

```bash
docker compose up -d db
pip install -r requirements.txt
python ml/train.py
pytest
```

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
