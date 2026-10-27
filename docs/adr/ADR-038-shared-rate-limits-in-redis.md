# Decision

Store the rate-limit counters in Redis whenever `REDIS_URL` is set, using a counter per caller per minute. If Redis is unreachable, let requests through.

## Context

The token bucket from v5 lives in each API process's memory. We measured it in v5: with 2 processes and a limit of 5, **8** requests got through. In Kubernetes the API runs as 3 copies, so the real limit would be up to 3 times the configured one.

This was the trigger for Redis that was written in v5. It fired now, in v16.

## Options

1. **Keep the token bucket and pin each user to one copy (sticky sessions).** This is fragile: a restarted copy loses its counters, and the load is spread unevenly.
2. **A token bucket in Redis.** It needs a Lua script to read and write in one step, which is too complex for a beginner codebase.
3. **A fixed one-minute counter in Redis, using `INCR` and `EXPIRE`.** This is the option we chose.

## Decision

Option 3, in `RedisRateLimiter` in `app/core/rate_limit.py`:

- The key is `ratelimit:<budget>:<caller>:<minute number>`.
- `INCR` adds one and returns the new value in one step, so two copies can never both read "4".
- The first increment of each key sets `EXPIRE 60`, so Redis cleans up by itself.
- A count above the limit gives a 429, with `Retry-After` set to the seconds left in the minute.
- **Fail open:** if Redis is down, the request is allowed and a warning is logged.
- When `REDIS_URL` is empty, the v5 in-memory bucket is still used. Tests and single-process runs need no Redis.

## Why?

- It takes two Redis commands and about 20 lines of code, and a test with a fake Redis shows the v5 bug fixed: 2 copies, limit 5, 8 requests → only 5 allowed.
- Fail open: the limit protects the service, so it should never be the reason the service is down.

## Trade-offs

- **Gained:** one budget per caller, however many API copies run.
- **Lost:**
  - The fixed window allows a burst of up to twice the limit across a minute boundary. The token bucket did not.
  - While Redis is down there is no limit at all. The log line is the only signal.
  - There is one more container to run.

## Future Trigger

- **Bursts across the boundary actually matter**, for example on paid API quotas: move to a sliding window or a token bucket in a Lua script.
- **Redis becomes important for other things,** such as sessions or a cache: give it persistence, and watch it.
