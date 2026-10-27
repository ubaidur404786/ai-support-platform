# Decision

Add a small React app (built with Vite, in plain JavaScript), served by nginx. The same nginx forwards `/api/...` to the API.

## Context

Until v16 the only way to use the platform was through API calls with curl, or the FastAPI `/docs` page. A support agent cannot do that, and a demo video of curl commands does not show the product.

## Options

1. **The FastAPI `/docs` page.** It is already there, but it is a developer tool, not a product.
2. **Server-rendered HTML templates inside the API.** There is no build step, but the API would mix UI and backend, and streaming answers is awkward.
3. **A separate React app as its own container.** This is the option we chose.

## Decision

- `frontend/` is a Vite and React project with three screens in one page:
  - log in or create an account
  - upload documents and watch their status change from `queued` to `ready`
  - ask a question and watch the answer appear as it is written, with its sources
- It is plain JavaScript, with no TypeScript, router or state library. `src/api.js` holds every call to the backend.
- The streaming answer is read line by line from `POST /answers/stream` (NDJSON, v11), using `fetch` and `response.body.getReader()`.
- The image is built in two stages: Node builds the files, then a small unprivileged nginx serves them. Node never ships.
- The page always calls `/api/...`. nginx forwards it to the API (ADR-035), and the Vite dev server does the same during development. The same build works on the laptop, in Compose, in Kubernetes and on AWS.

## Why?

- It is the simplest UI that shows every part of the backend working: authentication, the background worker, retrieval and streaming generation.
- Running it as its own container shows a fifth independently deployable service, with a real reason to exist.

## Trade-offs

- **Gained:** the product can be used and recorded, and the web app is the only thing exposed to the outside.
- **Lost:**
  - a Node toolchain to keep updated
  - the access token lives in memory, so refreshing the page logs you out (simple and safe, but not convenient)
  - no automated UI tests; the UI is checked by hand in a browser

## Future Trigger

- **The UI grows beyond a few screens:** add a router and component tests.
- **Users complain about being logged out:** add refresh tokens (still open debt (k) from v4).
