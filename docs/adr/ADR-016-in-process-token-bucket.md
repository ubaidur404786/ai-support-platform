# ADR-016 — In-process token bucket for rate limiting

Status: accepted (v5)

## Context

After v4, one caller could spend an unbounded amount of CPU: a wrong login costs a full bcrypt
computation (893 ms P50 in the v5 session), `/auth/login` must be reachable anonymously, and
`/classify` was public. ADR-011 bounded the work *per request*; nothing bounded requests *per
caller*. Measured: 30 wrong logins from one client kept the server busy for 29.06 s.

We need a per-caller limit that runs before the expensive work. Two questions: which algorithm,
and where the counters live.

## Options

Algorithm:

1. **Fixed window** — count per caller per clock minute. One integer, but a caller can send the
   full limit at 0:59 and again at 1:00: double the limit in two seconds.
2. **Sliding log** — store each request's timestamp, count those in the last minute. Exact, but
   memory per caller grows with the limit.
3. **Token bucket** — two numbers per caller (tokens, last update); tokens refill continuously.
   Allows a burst up to the capacity, then holds the caller to the refill rate.

Storage:

A. **A dict in process memory**, guarded by a lock.
B. **Redis** — shared by every process, atomic via `INCR` or a Lua script.
C. **PostgreSQL** — shared, but adds a write to every limited request.
D. **A library** (`slowapi` / `limits`) over either A or B.
E. **A reverse proxy** (nginx `limit_req`) in front of the app.

## Decision

**Token bucket (3), in process memory (A), written directly** — `app/core/rate_limit.py`, about
80 lines. Buckets live on `app.state.rate_limiters`, built in `create_app`. The dict is capped at
100,000 callers, evicting the least recently seen.

## Why?

- **The platform runs as one process.** For one process a locked dict is correct, and costs
  **2.07 µs** per check (measured). Redis would add a network round trip to every limited request,
  a container to run, and a new failure mode — "Redis is down, do we fail open or closed?" — to
  solve a problem we do not have yet.
- **Token bucket has no window edge to exploit** and matches how people actually use the API: a
  quiet caller can burst, a looping one is throttled. Lazy refill means no timer thread per caller.
- **Written directly rather than via `slowapi`**, because the whole mechanism is visible in one
  file and testable with an injected clock — time is moved by assignment, so tests are exact and
  take no wall time. A library would hide exactly the part worth understanding, for a feature this
  small. This is revisited when storage moves to Redis, where a library's atomic scripts earn
  their place.
- **Not a reverse proxy**, because the inference limit is keyed on the *user id*, which only the
  application knows after verifying the token. A proxy can limit by address; it cannot limit by
  account.
- **Built per app instance**, so every test gets empty buckets and cannot be refused because a
  previous test spent the budget.

## Trade-offs

Gain: bounded per-caller CPU; a refused login costs ~6 ms instead of ~890 ms; no new
infrastructure; microsecond overhead; deterministic tests.

Lose:

- **Per-process limits.** With `--workers 2`, a limit of 5 let **8** attempts through (measured;
  up to 10 possible). Every worker or replica adds a full budget.
- **Buckets are forgotten on restart.** The worst case is a fresh budget per caller.
- **Eviction is a leak by design.** A caller evicted at the 100,000 cap gets a fresh budget.
  Chosen over unbounded memory (~14.2 MB measured at the cap).
- **Burst and rate are one number.** `capacity = N`, `refill = N/min`. Separating them (e.g. burst
  3, rate 10/min) is easy but not yet needed.

## Future Trigger

- **The first time the API runs as more than one process** — `--workers N`, or a second replica
  behind a load balancer. Move buckets to Redis with an atomic check-and-take (a Lua script or
  `INCR` + `EXPIRE`), and decide explicitly whether a Redis outage fails open (availability) or
  closed (protection). For login, failing closed is probably right; for inference, open.
- **A distributed attack** — many addresses, each under its limit. Add a global cap on concurrent
  bcrypt work (a semaphore), which protects the CPU regardless of who is asking.
- **Different burst and rate requirements** — split `capacity` from `refill_per_second` in config.
