"""Tests for per-caller rate limits.

The first half tests the token bucket on its own, with a fake clock: time is
moved forward by assignment instead of by sleeping, so the tests are instant and
exact. The second half tests the limits through HTTP, on apps built with small
limits so a budget can be exhausted in a handful of requests.
"""

import threading

import pytest
from fastapi.testclient import TestClient

from app.auth.service import AuthService
from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.main import create_app
from tests.conftest import _register_and_login


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build_limiter(capacity=3, refill_per_second=1.0, max_keys=100_000):
    clock = FakeClock()
    limiter = RateLimiter(
        capacity=capacity,
        refill_per_second=refill_per_second,
        clock=clock,
        max_keys=max_keys,
    )
    return limiter, clock


# --- The algorithm, no HTTP ---------------------------------------------------


def test_a_full_bucket_allows_a_burst_then_refuses():
    limiter, _ = build_limiter(capacity=3)

    results = [limiter.acquire("maya") for _ in range(4)]

    # Three allowed (0.0 = go ahead), the fourth told to wait.
    assert results[:3] == [0.0, 0.0, 0.0]
    assert results[3] > 0


def test_the_wait_is_exactly_the_time_until_the_next_token():
    limiter, _ = build_limiter(capacity=1, refill_per_second=0.5)

    limiter.acquire("maya")

    # One token every 2 seconds, and the bucket was just emptied.
    assert limiter.acquire("maya") == pytest.approx(2.0)


def test_tokens_come_back_over_time():
    limiter, clock = build_limiter(capacity=2, refill_per_second=1.0)
    limiter.acquire("maya")
    limiter.acquire("maya")
    assert limiter.acquire("maya") > 0

    clock.now += 1.0

    assert limiter.acquire("maya") == 0.0
    # Only one token came back, not a full bucket.
    assert limiter.acquire("maya") > 0


def test_a_long_silence_never_earns_more_than_the_capacity():
    """Otherwise a caller idle for a day could save up 86,400 requests."""
    limiter, clock = build_limiter(capacity=3, refill_per_second=1.0)
    limiter.acquire("maya")

    clock.now += 86_400

    allowed = sum(limiter.acquire("maya") == 0.0 for _ in range(10))
    assert allowed == 3


def test_a_refused_request_does_not_spend_a_token():
    """A caller hammering an empty bucket must not push their wait further out."""
    limiter, clock = build_limiter(capacity=1, refill_per_second=1.0)
    limiter.acquire("maya")
    for _ in range(100):
        limiter.acquire("maya")

    clock.now += 1.0

    assert limiter.acquire("maya") == 0.0


def test_callers_have_separate_buckets():
    limiter, _ = build_limiter(capacity=1)

    assert limiter.acquire("maya") == 0.0
    assert limiter.acquire("maya") > 0
    # Maya running out has no effect on Bob.
    assert limiter.acquire("bob") == 0.0


def test_memory_is_bounded_by_evicting_the_least_recent_caller():
    limiter, _ = build_limiter(capacity=1, max_keys=2)
    limiter.acquire("a")
    limiter.acquire("b")
    limiter.acquire("c")  # evicts "a", the least recently seen

    assert len(limiter._buckets) == 2
    assert "a" not in limiter._buckets
    # The accepted cost of the bound: an evicted caller is forgotten, and gets
    # a fresh budget.
    assert limiter.acquire("a") == 0.0


def test_concurrent_requests_cannot_overspend_the_bucket():
    """Without the lock, threads could all read "1 token left" and all pass.

    FastAPI runs plain "def" endpoints in a thread pool, so this is the
    situation the limiter is actually in.
    """
    limiter = RateLimiter(capacity=10, refill_per_second=0.001)
    allowed = []
    start = threading.Barrier(50)

    def worker():
        start.wait()  # release all 50 threads at once
        if limiter.acquire("maya") == 0.0:
            allowed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(allowed) == 10


@pytest.mark.parametrize("capacity, refill", [(0, 1.0), (1, 0.0), (1, -1.0)])
def test_a_limiter_that_could_never_allow_anything_is_rejected(capacity, refill):
    with pytest.raises(ValueError):
        RateLimiter(capacity=capacity, refill_per_second=refill)


# --- Through HTTP ---------------------------------------------------------------


def app_with_limits(**overrides) -> TestClient:
    return TestClient(create_app(Settings(**overrides)))


LOGIN = {"email": "user@acme.example", "password": "fixture-password-long-enough"}


def test_login_is_refused_with_429_after_the_budget(clean_database):
    # _register_and_login spends 2 of the 4 (one register, one login).
    with app_with_limits(auth_rate_limit_per_minute=4) as client:
        _register_and_login(client, "Acme", "user@acme.example")

        assert client.post("/auth/login", json=LOGIN).status_code == 200
        assert client.post("/auth/login", json=LOGIN).status_code == 200
        refused = client.post("/auth/login", json=LOGIN)

    assert refused.status_code == 429
    assert refused.json()["detail"] == "Too many requests"
    # 4 per minute = one token every 15 s. Not asserted as exactly 15: the
    # bcrypt calls above took real seconds, and part of a token refilled.
    assert 1 <= int(refused.headers["Retry-After"]) <= 15


def test_a_refused_login_never_reaches_bcrypt(clean_database, monkeypatch):
    """The point of the whole version: the refusal must happen BEFORE the
    expensive work. A limiter that ran after the password check would still
    spend ~680 ms on every refused attempt."""
    calls = []
    original = AuthService.authenticate

    def counting_authenticate(self, email, password):
        calls.append(email)
        return original(self, email, password)

    monkeypatch.setattr(AuthService, "authenticate", counting_authenticate)

    with app_with_limits(auth_rate_limit_per_minute=2) as client:
        _register_and_login(client, "Acme", "user@acme.example")
        responses = [client.post("/auth/login", json=LOGIN) for _ in range(5)]

    assert [r.status_code for r in responses] == [429] * 5
    # Only the fixture's own login ran authenticate.
    assert len(calls) == 1


def test_wrong_passwords_spend_the_same_budget(clean_database):
    """Guessing is exactly what the limit exists to slow down."""
    with app_with_limits(auth_rate_limit_per_minute=3) as client:
        wrong = {"email": "nobody@acme.example", "password": "guess-number-one-x"}
        statuses = [client.post("/auth/login", json=wrong).status_code for _ in range(4)]

    assert statuses == [401, 401, 401, 429]


def test_registration_spends_the_auth_budget_too(clean_database):
    """Register also runs bcrypt, so it must not be an unlimited side door."""
    with app_with_limits(auth_rate_limit_per_minute=1) as client:
        first = client.post(
            "/auth/register",
            json={
                "organization_name": "Acme",
                "email": "a@acme.example",
                "password": "a-long-enough-password",
            },
        )
        second = client.post(
            "/auth/register",
            json={
                "organization_name": "Acme",
                "email": "b@acme.example",
                "password": "a-long-enough-password",
            },
        )

    assert first.status_code == 201
    assert second.status_code == 429


def _authenticated(client: TestClient, organization: str, email: str) -> dict:
    token = _register_and_login(client, organization, email)
    return {"Authorization": f"Bearer {token}"}


def test_classify_and_ticket_submission_share_one_inference_budget(clean_database):
    with app_with_limits(inference_rate_limit_per_minute=3) as client:
        client.headers.update(_authenticated(client, "Acme", "user@acme.example"))
        text = {"text": "I was charged twice"}

        statuses = [
            client.post("/classify", json=text).status_code,
            client.post("/classify", json=text).status_code,
            client.post("/tickets", json=text).status_code,
            # Budget spent: neither route may be used to get around the other.
            client.post("/tickets", json=text).status_code,
            client.post("/classify", json=text).status_code,
        ]
        listed = client.get("/tickets").json()

    assert statuses == [200, 200, 201, 429, 429]
    # The refused submission stored nothing.
    assert listed["total"] == 1


def test_reads_do_not_spend_the_inference_budget(clean_database):
    with app_with_limits(inference_rate_limit_per_minute=1) as client:
        client.headers.update(_authenticated(client, "Acme", "user@acme.example"))
        client.post("/classify", json={"text": "I was charged twice"})

        assert client.post("/classify", json={"text": "again please"}).status_code == 429
        assert client.get("/tickets").status_code == 200


def test_each_user_has_their_own_inference_budget(clean_database):
    """Keyed on the user, so two colleagues in one organisation - who may well
    share an office network address - do not use up each other's budget."""
    with app_with_limits(inference_rate_limit_per_minute=1) as client:
        maya = _authenticated(client, "Acme", "maya@acme.example")
        bob = _authenticated(client, "Acme", "bob@acme.example")
        text = {"text": "I was charged twice"}

        assert client.post("/classify", json=text, headers=maya).status_code == 200
        assert client.post("/classify", json=text, headers=maya).status_code == 429
        assert client.post("/classify", json=text, headers=bob).status_code == 200


def test_an_anonymous_caller_gets_401_not_429(clean_database):
    """Authentication runs first: an anonymous caller has no budget to spend."""
    with app_with_limits(inference_rate_limit_per_minute=1) as client:
        statuses = {
            client.post("/classify", json={"text": "I was charged twice"}).status_code
            for _ in range(3)
        }

    assert statuses == {401}


def test_limits_can_be_switched_off(clean_database):
    with app_with_limits(rate_limit_enabled=False, auth_rate_limit_per_minute=1) as client:
        wrong = {"email": "nobody@acme.example", "password": "guess-number-one-x"}
        statuses = {client.post("/auth/login", json=wrong).status_code for _ in range(3)}

    assert statuses == {401}
