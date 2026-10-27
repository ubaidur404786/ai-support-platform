# Version

**v17-react-frontend**: a small React web app, which is the one public entry point to the whole system.

## Problem

Everything up to v16 is usable only with `curl`, the FastAPI `/docs` page, or a Python script.

Picture Sara, a support agent. She wants to drop the refund policy PDF in, wait until it's processed, and ask "Can I get my money back?". She will not write a multipart POST request with a bearer token.

There is also nothing to show in a demo video except terminal windows.

On top of that, nothing in the Kubernetes cluster from v16 is reachable from outside at all: every Service is internal.

## Current Architecture

The API (3 copies), worker, model service, embedding service, PostgreSQL and Redis all run on k3s, with only internal Services.

## Why the Old Design Is Not Enough

- **No user interface.** The product cannot be used by its real users.
- **No way into the cluster.**
- **If we simply exposed the API, two things would break.**
  - The browser page and the API would be on different addresses, so we would need CORS settings.
  - The model services would still be internal, but the API's per-address login limit would see one address for everyone once traffic arrives through a proxy.

## Solution

**`frontend/`** is a Vite + React app in plain JavaScript (ADR-039):

| File | What it does |
|---|---|
| `src/api.js` | every call to the backend, always to `/api/...`. `askStream()` reads `POST /answers/stream` line by line with `fetch` + `getReader()` |
| `src/App.jsx` | shows the login screen until there is a token, then the main page |
| `src/components/Login.jsx` | log in, or create an organisation and account |
| `src/components/Documents.jsx` | upload a `.txt`, `.md` or `.pdf`; the list asks for updates every 2 s while any document is `queued` or `processing` |
| `src/components/Ask.jsx` | ask a question; the text appears as it is written; the sources are listed with their scores; "Nothing in your documents answers this question" when the API says so |
| `src/components/Status.jsx` | one line at the bottom showing the API, database, answer model and search model, from the API's `/health` (v14) |

**`frontend/Dockerfile`** is a two-stage build. Node builds the static files, and a small unprivileged nginx image serves them. The image is **74 MB**.

**`frontend/nginx.conf`** forwards `/api/...` to `http://api:8000/` (ADR-035):

- `proxy_buffering off` and a 180 s read timeout, so streamed answers reach the browser line by line.
- `client_max_body_size 6m` for uploads.

**The API runs uvicorn with `--proxy-headers`.** It now sees the user's address from `X-Forwarded-For` instead of nginx's. That is safe only because nothing but our nginx can reach the API.

**`k8s/web.yaml`**: one copy, `NodePort` 30080. It is the only thing reachable from outside the cluster. **`docker-compose.yml`** gets a `web` service on 8080, and the CI matrix builds `support-web` too.

## Why This Solution?

- **React + Vite** is the most common setup for a small single-page app, and the build output is plain files that any web server can serve.
- **Plain JavaScript, no router or state library:** three screens do not need them, and beginners can read every line.
- **nginx in front, with the same origin for the page and the API:** no CORS, and one public port.
- **Simpler alternative:** FastAPI's `/docs` page. It already exists, but it is a developer tool, not a product.

## New Trade-offs

- **A Node toolchain** to keep updated. `npm install` reported known vulnerabilities in the development dependencies; they are not in the built files, but they still need to be followed up.
- **The token lives only in memory,** so refreshing the page logs you out. It is simple, and nothing sensitive is stored in the browser, but it is not convenient.
- **No automated UI tests.** The UI was checked over HTTP (below) but not clicked through in a browser in this session: the browser automation was not connected.
- **`--proxy-headers` would be a security hole** if the API were ever exposed directly. A comment next to the setting says so.

## What Changed

| File | Change |
|---|---|
| `frontend/` | new: `package.json`, `vite.config.js`, `index.html`, `src/` (6 files), `nginx.conf`, `Dockerfile` |
| `k8s/web.yaml` | new: Deployment + NodePort 30080; added to `kustomization.yaml` |
| `docker-compose.yml` | `web` service on 8080; restart policies |
| `docker/api/Dockerfile` | `--proxy-headers --forwarded-allow-ips *` |
| `.github/workflows/ci.yml` | `support-web` in the image matrix |
| `.dockerignore` | leaves out `frontend/node_modules` |
| `k8s/aws/kustomization.yaml` | new: the same files with Docker Hub image names, ready for v18 |
| `app/core/config.py` | version 0.17.0 |

## How to Test

In development (with the API on :8000):

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

With Docker Compose:

```bash
docker compose up -d --build
```

Open http://localhost:8080.

On Kubernetes (see `k8s/README.md`):

```bash
kubectl apply -k k8s
```

Open http://localhost:30080, and run the smoke test through nginx:

```bash
python scripts/smoke_test.py http://localhost:30080/api
```

## Expected Result

1. The login page appears, with a status line at the bottom showing `API: ok`, `answer model: ok` and `search model: ok`.
2. After you create an account, the main page appears.
3. An uploaded article shows `queued`, then `processing`, then `ready` on its own.
4. A question shows "Reading your documents…". The answer then appears piece by piece, followed by its sources.

## Measurements

Measured locally on k3s in Docker on the CPU laptop, through the web app's NodePort:

| Check | Result |
|---|---|
| `GET /` | 200, the page with the title "Support Assistant" |
| JS bundle | 148,683 bytes (48 KB gzipped). It contains every screen ("Ask a question", "Knowledge base", "Create an account") |
| A deep link (`/some/deep/link`) | 200 (nginx falls back to `index.html`) |
| Smoke test through nginx | register + log in 2.06 s, upload → ready 2.38 s, streamed answer 42.8 s (cold model) |
| **Streaming through nginx** | 24 text lines arrived spread over **2.4 s** (first at 26.7 s, last at 29.1 s), so nginx does not buffer the stream |
| Web container memory | 11–14 MiB |
| `support-web` image | 74 MB |

Things to be honest about:

- **I didn't click through the UI in a real browser.** Everything above was checked over HTTP, from the page down to the exact API calls the page makes.
- **The model reads the prompt for most of the wait** (26.7 s of 29.1 s here). Streaming shows the words as soon as they exist, but on this laptop, inside Docker's VM, that is still a long wait.
- **Early in the session, a smoke test through Docker Compose timed out after 120 s with no text.** The laptop had 40 MB of free memory, because Kubernetes was trying to start at the same time. It worked once memory was free.

## Interview Explanation

> "The backend was complete but only usable through curl, and nothing in the cluster was reachable from outside. I added a small React app in plain JavaScript: log in, upload documents and watch them become ready, and ask questions with the answer streamed in as it's generated. It reads the NDJSON stream with fetch and a reader. It's served by nginx, which also proxies /api to the API service, so the browser sees one origin. That means no CORS, and the web app is the only public entry point. I had to turn on proxy headers in uvicorn, otherwise every user would share one login rate limit, the proxy's. That's safe only because the API isn't reachable except through nginx. I checked that nginx doesn't buffer the stream: 24 pieces arrived over 2.4 seconds, not all at the end."

## Next Possible Limitation

- **It only runs on this laptop.** The images are not on Docker Hub yet (v15 needs a token with write access) → **v18-aws-deploy**: k3s on one EC2 machine, pulling from Docker Hub, with the same `k8s/` files and `k8s/aws/kustomization.yaml`.
- **Answers are slow on this laptop's Docker VM** (cold first answer about 40 s).
- **No browser-level automated test.**
