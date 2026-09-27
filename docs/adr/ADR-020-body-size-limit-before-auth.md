# ADR-020 — Refuse oversized request bodies before authentication

Status: accepted (v6)

## Context

v5 established a rule: refuse a request *before* the expensive work. For logins, the rate limit is
a dependency that runs before bcrypt.

v6 added file uploads, and measurement showed that the rule did not hold for them. FastAPI parses a
`multipart/form-data` body **before** it resolves any dependency, including `get_current_user`
and the rate limits. An anonymous caller sending a large file had it received and written to a
temporary file in full, and only then was answered 401:

| Anonymous upload | Answer | Time |
|---|---|---|
| 1 KB | 401 | 79 ms |
| 50 MB | 401 | 729 ms |
| 200 MB | 401 | 4,146 ms |

The size check inside the endpoint cannot help either, because it runs after the body has already
been received.

## Options

1. Keep the check inside the endpoint only.
2. **An HTTP middleware** that reads `Content-Length` and refuses before routing.
3. A reverse proxy limit (e.g. nginx `client_max_body_size`).
4. Stream the upload manually from `request.stream()`, authenticating first.

## Decision

Option 2: `app/core/request_limits.py`, installed in `create_app`, refuses any request whose
declared `Content-Length` exceeds `MAX_DOCUMENT_BYTES` + 64 KB with 413, **without reading the
body**. The endpoint check stays as a second line of defence.

## Why?

- **It runs before body parsing and before every dependency**, so it holds for anonymous callers.
- **Measured:** with only headers sent, declaring 200 MB or 1 GB, the answer is 413 in **3.2 ms /
  1.4 ms**, with zero body bytes read.
- **A reverse proxy (3) is the complete answer, but there is no proxy yet.** When one arrives it
  should carry this limit too, and it will also cover the case below.
- **Manual streaming (4)** would give up FastAPI's form handling and validation for every upload
  endpoint, a lot of complexity for a problem a header check solves.
- **One global ceiling is enough today:** the largest legitimate request is a document upload.

## Trade-offs

Gain: oversized uploads cost the server about 2 ms regardless of who sends them; the v5 rule
("refuse before the work") now holds for bodies too.

Lose:

- **Requests without `Content-Length` (chunked transfer encoding) are not covered.** Their size is
  unknown until read. The endpoint still reads at most 5 MB + 1 byte, so *processing* stays
  bounded, but what is *received* is not. Real gap; a proxy's job.
- **One limit for every endpoint.** A JSON endpoint accepts bodies up to ~5 MB, far larger than any
  JSON request needs. Pydantic's field limits still bound what is processed.
- **The client may keep sending after the 413.** Measured from the client side, httpx took 2.3 s to
  finish pushing a 200 MB body that the server had already refused. The server does no work for
  it, but bandwidth is still consumed.

## Future Trigger

- **A reverse proxy or load balancer is introduced** → enforce the body limit there as well,
  covering chunked requests.
- **Endpoints with very different size needs** → per-route limits instead of one global ceiling.
- **Uploads move to v7's background processing** → consider direct-to-object-storage uploads
  (presigned URLs), which take large bodies off the API process entirely.
