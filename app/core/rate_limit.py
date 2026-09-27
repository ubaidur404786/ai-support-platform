"""Per-caller request limits, using the token bucket algorithm.

Why this exists: pagination (v3) bounded how much work ONE request can cause.
Nothing bounded how many requests one caller can send. A login costs ~680 ms of
CPU for bcrypt and a classification ~1.7 ms of model inference, so a single
caller in a loop could keep the server busy on their behalf alone.

The token bucket, in one picture: every caller has a bucket holding at most
`capacity` tokens. Each request takes one token. Tokens drip back in at a fixed
rate. An empty bucket means "wait" - the request is refused with 429 before any
expensive work runs. A caller who has been quiet can burst up to `capacity`
requests at once, which is what a real person clicking around looks like; a
caller in a loop is held to the refill rate, which is what abuse looks like.

This limiter lives in the memory of ONE process. With several uvicorn workers,
each has its own buckets, so the real limit is workers x the configured limit.
That is the trigger for moving the counters into a shared store such as Redis -
not needed while the platform runs as a single process.
"""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request, status


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    def __init__(
        self,
        capacity: int,
        refill_per_second: float,
        # Injected so tests can move time forward instead of sleeping.
        clock: Callable[[], float] = time.monotonic,
        # Every distinct caller costs a little memory. Without a ceiling, an
        # attacker rotating through addresses could grow this dict until the
        # process runs out of memory - trading one resource exhaustion for another.
        max_keys: int = 100_000,
    ) -> None:
        if capacity < 1 or refill_per_second <= 0:
            raise ValueError("capacity must be >= 1 and refill_per_second > 0")
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}
        # Plain "def" endpoints run in a thread pool, so two requests from the
        # same caller can arrive at the same moment. Without the lock, both could
        # read "1 token left" and both be let through.
        self._lock = threading.Lock()

    def acquire(self, key: str) -> float:
        """Take one token for `key`.

        Returns 0.0 if the request may proceed, otherwise how many seconds until
        a token is available - which becomes the Retry-After header.
        """
        now = self._clock()
        with self._lock:
            # pop + re-insert keeps the dict ordered from least to most recently
            # seen, so the first key is always the right one to evict.
            bucket = self._buckets.pop(key, None)
            if bucket is None:
                bucket = _Bucket(tokens=self._capacity, updated_at=now)
                if len(self._buckets) >= self._max_keys:
                    # Evicting a bucket forgets that caller's history, which
                    # gives them a fresh budget. Accepted: bounded memory matters
                    # more than perfect accuracy for the least recent caller.
                    del self._buckets[next(iter(self._buckets))]
            else:
                # Refill lazily for the time that passed since the last request,
                # instead of running a timer per caller.
                elapsed = now - bucket.updated_at
                bucket.tokens = min(
                    self._capacity, bucket.tokens + elapsed * self._refill_per_second
                )
                bucket.updated_at = now
            self._buckets[key] = bucket

            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return 0.0
            return (1 - bucket.tokens) / self._refill_per_second


def per_minute(requests_per_minute: int) -> RateLimiter:
    """A bucket that holds one minute's allowance and refills over one minute."""
    return RateLimiter(
        capacity=requests_per_minute, refill_per_second=requests_per_minute / 60
    )


def enforce(request: Request, name: str, key: str) -> None:
    """Refuse the request with 429 if `key` has used up the `name` budget.

    Limiters live on app.state, created in create_app, so every app instance -
    including each one a test builds - starts with empty buckets. A missing
    limiter means limits are switched off in configuration.
    """
    limiter: RateLimiter | None = request.app.state.rate_limiters.get(name)
    if limiter is None:
        return

    retry_after = limiter.acquire(key)
    if retry_after > 0:
        raise HTTPException(
            # 429 Too Many Requests: the request is valid, the caller is sending
            # too many of them. Not 503 - the server is fine; this caller is not.
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            # Tells a well-behaved client exactly how long to wait, so it does
            # not have to guess and retry in a tight loop. Rounded up: saying
            # "retry in 0 s" would invite an immediate second refusal.
            headers={"Retry-After": str(math.ceil(retry_after))},
        )
