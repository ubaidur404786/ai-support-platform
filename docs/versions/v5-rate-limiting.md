# v5 — Per-caller rate limits

API 0.5.0 · model v0.1.0 unchanged · 120 tests passing

![v5 architecture](../architecture/v5.svg)

## Recap: where v4 left us

Maya is a support agent at Acme. On Monday morning she logs in once, classifies about twenty
tickets, and goes to lunch. That is what normal use looks like: **one login, a few dozen model
calls an hour.**

After v4, a script could do something very different. It could send `POST /auth/login` with a
guessed password in a loop, forever, from one laptop. Every attempt cost the server **682 ms of
bcrypt** (measured in v4), and nothing stopped the next one. Thirty guesses kept one CPU core
busy for about thirty seconds — spent entirely on a caller who had no account. And
`POST /classify` needed no account at all.

v3 had already fixed the same *shape* of problem for rows: the caller was choosing how much work
the server did, so v3 put a ceiling on the page size. But v3 bounded the work **per request**.
Nothing bounded the number of requests **per caller**. v5 is that second ceiling.

## Problem

One caller could spend an unbounded amount of the server's CPU.

- `POST /auth/login` and `POST /auth/register` run bcrypt at cost factor 12. In this session a
  wrong login measured **893 ms P50** (v4's 682 ms was a different session; standing rule 1).
  Both endpoints are unauthenticated by necessity — they are how you get authenticated.
- `POST /classify` was public. It stores nothing, so v4 left it open, but every call runs model
  inference.
- `POST /tickets` runs the same inference behind a token, with no ceiling on how often.

Measured: **30 wrong logins from one client took 29.06 s**, every one of them answered with a
full bcrypt computation.

## Current Architecture

Before this version:

```
Client → router.py → Depends(get_current_user) → service.py → repository.py → PostgreSQL
                                                     └→ classifier.predict()
```

Bounded pages (v3), a known caller on `/tickets` (v4). Anonymous on `/auth/*` and `/classify`.

## Why the Old Design Is Not Enough

- **Pagination limits one request, not the stream of them.** ADR-011 already recorded this:
  "pagination does not protect against request frequency."
- **Authentication identifies callers; it does not limit them.** A logged-in user in a loop was
  just as able to exhaust inference as an anonymous one — only now we knew their name.
- **bcrypt is slow on purpose**, and cannot be made fast without making passwords easy to crack.
  The cost cannot be reduced, so the number of times we pay it has to be.

## Solution

A **token bucket per caller**, checked by a FastAPI dependency **before** the expensive work.

```
POST /auth/login ──► limit_auth_attempts ──► key = client address ──► "auth" bucket (10/min)
                          │ empty → 429 + Retry-After            │ token → bcrypt → 200/401

POST /classify   ──► get_current_user ──► limit_inference ──► key = "user:<id>" ──► "inference" bucket (60/min)
POST /tickets    ──┘      │ no token → 401        │ empty → 429 + Retry-After   │ token → predict → 200/201
```

**How a token bucket works.** Every caller has a bucket holding at most *N* tokens. A request
takes one. Tokens drip back at *N* per minute. An empty bucket means `429 Too Many Requests`,
with a `Retry-After` header saying how many seconds until the next token. A caller who has been
quiet can burst *N* requests at once — that is what a person clicking around looks like. A caller
in a loop is held to the refill rate — that is what a script looks like.

Four decisions sit inside that:

1. **Auth endpoints are keyed on the client address.** There is no user yet — that is what the
   caller is trying to obtain.
2. **Inference is keyed on the user.** Colleagues behind one office network share an address, and
   should not share a budget. For that to work, `/classify` now **requires a token**.
3. **`/classify` and `POST /tickets` share one budget.** Both spend the classifier's CPU; separate
   budgets would just double the allowance and make one route a side door around the other.
4. **Reads are not limited in this version.** `GET /tickets` measured 33.8 ms in v4 and pages are
   already bounded. No measurement asks for it yet.

## Why This Solution?

### Why a token bucket, and not a simpler counter

| Algorithm | Memory per caller | Behaviour | Problem |
|---|---|---|---|
| Fixed window ("10 per clock minute") | 1 counter | resets at :00 | 10 at 0:59 + 10 at 1:00 = **20 in two seconds** |
| Sliding log (store every timestamp) | N timestamps | exact | memory grows with the limit |
| **Token bucket** | **2 numbers** | smooth refill, allows a burst of N | burst size and rate are one setting here |

The bucket is two floats per caller, refills lazily (no timer thread — it computes how much time
passed since the caller's last request), and has no window boundary to exploit.

### Why in-process memory, and not Redis

The platform runs as **one process**. A dict guarded by a lock is correct for one process,
costs **2.07 µs** per check (measured), and adds no infrastructure that can fail. Redis would be
correct for many processes — and we measured exactly what "many" does: with `--workers 2` a
limit of 5 let **8** attempts through, because each worker has its own dict. That is the recorded
trigger for a shared store, not a reason to add one today. See ADR-016.

### Why the check runs before the handler

A limiter that ran after the password check would still pay ~890 ms per refused attempt, and
protect nothing. As a FastAPI dependency it runs before the endpoint body. A test
(`test_a_refused_login_never_reaches_bcrypt`) proves `AuthService.authenticate` is never called
for a refused attempt, rather than trusting the ordering.

### Why 429, and why `Retry-After`

`429 Too Many Requests` says "your request is valid; you are sending too many". Not `503`, which
would blame the server, and not `403`, which would suggest a permission problem. `Retry-After`
tells a well-behaved client exactly how long to wait, so it does not retry in a tight loop and
make the problem worse. It is rounded **up**: "retry in 0 s" would invite an immediate second
refusal.

### Why the client address, and not `X-Forwarded-For`

`request.client.host` is whoever opened the TCP connection. `X-Forwarded-For` is a header — the
caller writes it. Trusting it with no proxy in front would let any script claim a new address on
every request and escape the limit completely. When a reverse proxy arrives, the proxy's header
(and only the proxy's) becomes trustworthy. See ADR-017.

## New Trade-offs

**Gained:** a refused login costs ~6 ms instead of ~890 ms; one caller can no longer spend the
server's CPU without bound; `/classify` is no longer anonymous; clients are told when to retry.

**Lost, or newly owed:**

- **The limits are per process.** N workers or N replicas multiply every limit by N — measured,
  not assumed (5 → 8 with two workers).
- **A per-user 429 is not free.** Keying on the user means the token check and the `users` lookup
  run before refusal: **14.8 ms** P50 for a refused `/classify`, against 6.2 ms for a refused
  login (address-keyed, no database).
- **Per-address limits do not stop a distributed attack.** 1,000 addresses × 10 logins a minute is
  still 10,000 bcrypt calls a minute. Stopping that needs a *global* ceiling on concurrent bcrypt
  work, or per-account protection (which brings its own risk: an attacker locking a victim out).
- **Shared-address users share the auth budget.** Ten colleagues behind one office NAT all
  logging in within a minute would hit the limit. At 10/min this is acceptable; with a proxy it
  gets worse until `X-Forwarded-For` is trusted correctly.
- **Restarting the server forgets every bucket.** Acceptable for a limiter: the worst case is one
  fresh budget per caller.
- **Memory is bounded by eviction, not by refusal.** At 100,000 callers the least recently seen
  one is forgotten and gets a fresh budget. Bounded memory (~14 MB measured) was chosen over
  perfect accuracy for the oldest caller.
- **`/classify` now needs an account**, which is a breaking change for any client that used it
  anonymously. There are none yet; there would be in production.
- **The test suite grew to 165.49 s** (from 102.92 s in v4, a different session): the eleven
  `/classify` tests now each register a user (~1.4 s of bcrypt each), plus nine new API tests.

## What Changed

| File | Change |
|---|---|
| `app/core/rate_limit.py` | **new** — `RateLimiter` (token bucket, lock, injectable clock, `max_keys` eviction), `per_minute()`, `enforce()` raising 429 + `Retry-After` |
| `app/core/config.py` | `app_version` 0.5.0; `rate_limit_enabled`, `auth_rate_limit_per_minute = 10`, `inference_rate_limit_per_minute = 60` |
| `app/main.py` | `create_app` builds `app.state.rate_limiters` — per app instance, so each test gets empty buckets |
| `app/auth/dependencies.py` | `limit_auth_attempts` (key = client address), `limit_inference` (key = `user:<id>`) |
| `app/auth/router.py` | the auth limit declared on the **router**, covering every `/auth` endpoint |
| `app/classification/router.py` | `/classify` requires a token and spends the inference budget |
| `app/tickets/router.py` | `POST /tickets` spends the same inference budget |
| `.env.example` | `RATE_LIMIT_ENABLED`, `AUTH_RATE_LIMIT_PER_MINUTE`, `INFERENCE_RATE_LIMIT_PER_MINUTE` |
| `tests/test_rate_limit.py` | **new** — 20 tests: 11 on the algorithm with a fake clock, 9 through HTTP |
| `tests/test_api.py` | `/classify` tests use an authenticated client; new `test_classify_requires_a_token` |

No migration: nothing new is stored in PostgreSQL.

## How to Test

```bash
docker compose up -d
```

```bash
pytest -v
```

```bash
pytest tests/test_rate_limit.py -v
```

Watch a login get refused (run the server with `uvicorn app.main:app`, then send 11 bad logins):

```bash
for i in $(seq 1 11); do curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/auth/login -H "Content-Type: application/json" -d '{"email":"nobody@acme.example","password":"wrong-password-guess"}'; done
```

Windows PowerShell:

```powershell
1..11 | ForEach-Object { try { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/login -ContentType "application/json" -Body '{"email":"nobody@acme.example","password":"wrong-password-guess"}' } catch { $_.Exception.Response.StatusCode.value__ } }
```

## Expected Result

- `pytest`: **120 passed**.
- The loop prints `401` ten times (slowly — each is bcrypt) and then `429` (instantly).
- A 429 carries `Retry-After: <seconds>` and `{"detail":"Too many requests"}`.
- `POST /classify` without a token returns `401`.

## Measurements

All measured locally in **one session**: one machine, `DB_ECHO=false`, no `--reload`, a Python
`httpx` client (standing rule 2), a single client address.

**30 wrong logins, sequential, one client** — `python scripts/measure_rate_limits.py logins --count 30`

| | Total | Allowed (401) | Refused (429) |
|---|---|---|---|
| `RATE_LIMIT_ENABLED=false` | **29.06 s** | 30, P50 891.3 ms | — |
| limits on, 10/min | **10.54 s** | 11, P50 893.3 ms | 19, **P50 6.2 ms** (min 4.2, max 9.4) |

Eleven, not ten: the first ten spent ~9 s in bcrypt, and at 10 tokens/minute ~1.5 tokens
refilled meanwhile. That is the bucket working, not an off-by-one.

**100 `/classify` calls after 5 warm-up, one user** — `python scripts/measure_rate_limits.py classify --count 100`

| | Allowed | Refused (429) |
|---|---|---|
| `RATE_LIMIT_ENABLED=false` | 100, P50 24.76 ms | — |
| limits on, 60/min | 57, P50 21.74 ms | 43, **P50 14.77 ms** |

57 = 60 − 5 warm-up + ~2 refilled during the run. The refused call is cheaper than an allowed one
but not free: the token and `users` lookup still run first (see *New Trade-offs*).

**The limiter itself** (in-process micro-benchmark, `timeit`):

| | |
|---|---|
| `acquire()`, one key, n=200,000 | **2.07 µs** per call |
| `acquire()`, 100,000 distinct keys | 3.12 µs per call |
| memory for 100,000 buckets (excluding key strings) | 14.2 MB |

**Per-process limits, measured** — limit 5/min, 40 wrong logins from 8 threads over 8 kept-alive
connections — `python scripts/measure_rate_limits.py parallel-logins --count 40`:

| | Allowed | Refused | Time |
|---|---|---|---|
| `--workers 1` | **5** | 35 | 1.46 s |
| `--workers 2` | **8** | 32 | 2.65 s |

With two workers the configured limit of 5 let 8 through (theoretical maximum 10, depending on
how the OS spread the connections). This also answers debt item (m): **multiple uvicorn workers
do start and serve on this Windows machine.**

**An instrument lesson, again.** The first parallel run created a new `httpx` client per request
and took 47.30 s for 40 requests; the same work over a shared client took 1.46 s. The time was the
client's connection setup, not the server. Standing rule 2 applied a second time.

**Not measured:** latency under sustained load from many addresses, and CPU utilisation directly.
The claims above are about request counts and per-request latency, not throughput.

## Interview Explanation

> After adding authentication, login became the most expensive endpoint — about 700 ms of bcrypt,
> by design — and it has to be reachable anonymously. Pagination had already bounded the work
> per request; nothing bounded the requests per caller. I added a token bucket per caller,
> enforced as a FastAPI dependency so it runs before the expensive work, returning 429 with
> Retry-After. Login is keyed on client address because there is no user yet; inference is keyed
> on user id, which meant `/classify` had to require a token, and it shares one budget with ticket
> submission so neither is a side door. Measured: 30 bad logins went from 29 s of server work to
> 10.5 s, and a refused attempt costs 6 ms instead of 890. The buckets are an in-memory dict,
> which is correct for one process — I measured that with two workers a limit of 5 let 8 through,
> so the move to Redis has a concrete trigger: the day we run more than one process. It also
> doesn't stop a distributed attack from many addresses; that needs a global concurrency cap.

## Next Possible Limitation

1. **Scaling out breaks the limits.** Any second worker or replica multiplies every limit —
   the trigger for moving buckets into a shared store (Redis).
2. **Distributed attacks.** Per-address limits do nothing against many addresses; a global cap on
   concurrent bcrypt work is the next defence.
3. **The knowledge base still does not exist.** Documents are the platform's purpose, and v4's
   ownership model was built for them. With per-caller limits in place, ingesting documents no
   longer opens an unbounded CPU path — the strongest candidate for v6.
4. Carried forward: no token revocation, no type checker, a 165 s test suite, unlimited reads,
   `total` still costing more than the page.
