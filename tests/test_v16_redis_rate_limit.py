"""v16: rate limits shared by every API copy through Redis.

FakeRedis keeps counters in a dict, so these tests need no Redis server.
Two RedisRateLimiter objects that share one FakeRedis are two API copies
sharing one Redis.
"""

from app.core.rate_limit import RedisRateLimiter


class FakeRedis:
    def __init__(self):
        self.counters = {}
        self.expiries = {}

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, seconds):
        self.expiries[key] = seconds


class BrokenRedis:
    def incr(self, key):
        raise ConnectionError("Redis is down")


def test_two_api_copies_share_one_budget():
    """The v5 problem, fixed: limit 5, two copies, 8 requests -> only 5 allowed."""
    shared = FakeRedis()
    copy_a = RedisRateLimiter(shared, "auth", limit=5, clock=lambda: 1000.0)
    copy_b = RedisRateLimiter(shared, "auth", limit=5, clock=lambda: 1000.0)

    results = [(copy_a if n % 2 else copy_b).acquire("10.0.0.7") for n in range(8)]

    assert results.count(0.0) == 5
    assert all(wait > 0 for wait in results[5:])


def test_the_refusal_says_how_long_until_the_next_minute():
    limiter = RedisRateLimiter(FakeRedis(), "auth", limit=1, clock=lambda: 1000.0)

    limiter.acquire("caller")
    # 1000 s is 40 s into minute 16; the next minute starts at 1020 s.
    assert limiter.acquire("caller") == 20.0


def test_a_new_minute_starts_a_new_budget():
    now = [1000.0]
    limiter = RedisRateLimiter(FakeRedis(), "auth", limit=1, clock=lambda: now[0])

    assert limiter.acquire("caller") == 0.0
    assert limiter.acquire("caller") > 0
    now[0] = 1021.0
    assert limiter.acquire("caller") == 0.0


def test_counters_expire_by_themselves():
    redis = FakeRedis()
    RedisRateLimiter(redis, "auth", limit=5, clock=lambda: 1000.0).acquire("caller")

    assert list(redis.expiries.values()) == [60]


def test_callers_and_budgets_are_counted_separately():
    redis = FakeRedis()
    auth = RedisRateLimiter(redis, "auth", limit=1, clock=lambda: 1000.0)
    answers = RedisRateLimiter(redis, "answers", limit=1, clock=lambda: 1000.0)

    assert auth.acquire("alice") == 0.0
    assert auth.acquire("bob") == 0.0
    assert answers.acquire("alice") == 0.0


def test_requests_are_allowed_when_redis_is_down():
    """Fail open: a broken rate-limit store must not take the whole API down."""
    limiter = RedisRateLimiter(BrokenRedis(), "auth", limit=1)

    assert limiter.acquire("caller") == 0.0
    assert limiter.acquire("caller") == 0.0
