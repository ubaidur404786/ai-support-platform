# ADR-032: Bound the model's queue and refuse early with 503 + Retry-After

Status: accepted (v12)

## Context

One model answers one question at a time: 5–10 answers a minute on this CPU (measured). Before v12,
every request waited for the lock, however many were ahead. At 8 simultaneous users the slowest
answer took 99 s (P95 93 s). A person gives up long before that, and the model still spends its CPU
on the abandoned answer.

## Options

1. **No limit** (before). Everyone is served, eventually.
2. **A timeout only**: give up after N seconds. The model may already be half-way through the
   answer, so the work is wasted, and the caller still waited N seconds.
3. **A bounded queue**: at most K answers running or waiting; the next one is refused at once with
   503 and `Retry-After`. This is called backpressure.
4. **A queue per organisation / priorities.** Fairer between tenants, but more code than the
   problem measured so far needs.

## Decision

Option 3, with K = `MODEL_MAX_QUEUE` = 4 and `Retry-After: 10`. The count lives in the model
service (`enter_queue()` / `leave_queue()` in `app/model_service/main.py`), so it covers every API
process. The API passes a "busy" answer on as its own 503 with the same `Retry-After`, for both
`/answers` and `/answers/stream` (before the first line). Every answer also logs its wait time and
its generation time.

## Why?

- **4 answers ≈ the longest reasonable wait.** At ~6–10 s an answer, the 4th in line waits ~25–30 s.
- **Refusing is cheap and honest.** Measured: a refusal returns in ≤ 1.1 s and never reaches the
  model. At 8 users, half the requests were refused and the slowest answered one took 38–52 s in two
  of three runs, instead of 93–99 s.
- **A queue place must never leak**: the place is freed in a `finally`, including when a streaming
  client disconnects. The streaming generator is started before it is returned, because a generator
  that never started does not run its `finally`. Tested after answers, streams and crashes.

## Trade-offs

Gained: a bounded worst case in most runs; fast, explicit "busy" instead of silence; `/health`
shows `in_queue`.

Lost: some users are turned away and must retry. The limit bounds **how many** wait, not **how
long**: when the laptop slowed and answers took 12–17 s, a request waited 48 s with 3 ahead (logged).
One 8-user run still had a 93 s maximum, before the timing log existed; its cause is unknown.
`threading.Lock` does not promise first-come-first-served order; the logs did not show a request
being overtaken, but it is not guaranteed.

## Future Trigger

- Tenants complain that one organisation fills the queue: per-organisation limits (option 4).
- Wait times in the log regularly above ~30 s: more capacity (a GPU, `v15-llm-serving`), not a
  longer queue.
- A deadline per request (drop answers whose caller has already gone) once clients send one.
