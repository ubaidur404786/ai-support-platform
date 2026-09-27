"""Measure what the v5 rate limits do to one caller in a loop.

Start the server first, without --reload and with DB_ECHO=false, then run from
the repository root:

    python scripts/measure_rate_limits.py logins --count 30
    python scripts/measure_rate_limits.py classify --count 100
    python scripts/measure_rate_limits.py parallel-logins --count 40 --threads 8

Run each scenario once with RATE_LIMIT_ENABLED=false and once with the limits on,
restarting the server in between so every bucket starts full. Numbers are only
comparable within one session on one machine.
"""

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

BASE_URL = "http://127.0.0.1:8000"
PASSWORD = "measure-password-long-enough"


def report(title: str, results: list[tuple[int, float]], elapsed: float) -> None:
    print(f"{title}: {len(results)} requests in {elapsed:.2f} s")
    by_status: dict[int, list[float]] = {}
    for status, ms in results:
        by_status.setdefault(status, []).append(ms)
    for status, values in sorted(by_status.items()):
        print(
            f"  {status}: n={len(values)}  P50 {statistics.median(values):.1f} ms"
            f"  min {min(values):.1f}  max {max(values):.1f}"
        )


def timed_post(client: httpx.Client, path: str, body: dict) -> tuple[int, float]:
    start = time.perf_counter()
    response = client.post(path, json=body)
    return response.status_code, (time.perf_counter() - start) * 1000


def wrong_login(client: httpx.Client) -> tuple[int, float]:
    # An unknown email still costs a full bcrypt verify (the dummy hash), so it
    # is exactly as expensive as guessing a real account's password.
    return timed_post(
        client,
        "/auth/login",
        {"email": "nobody@acme.example", "password": "wrong-password-guess"},
    )


def logins(count: int) -> None:
    # One client, kept alive: the instrument must be cheaper than the thing it
    # measures. A new client per request added seconds of connection setup.
    with httpx.Client(base_url=BASE_URL, timeout=60) as client:
        start = time.perf_counter()
        results = [wrong_login(client) for _ in range(count)]
        report("wrong logins, sequential", results, time.perf_counter() - start)


def parallel_logins(count: int, threads: int) -> None:
    # Several connections, so a multi-worker server can spread them across
    # processes - which is what exposes per-process buckets.
    with httpx.Client(
        base_url=BASE_URL, timeout=60, limits=httpx.Limits(max_connections=threads)
    ) as client:
        start = time.perf_counter()
        with ThreadPoolExecutor(threads) as pool:
            results = list(pool.map(lambda _: wrong_login(client), range(count)))
        report(f"wrong logins, {threads} threads", results, time.perf_counter() - start)


def classify(count: int, email: str) -> None:
    with httpx.Client(base_url=BASE_URL, timeout=60) as client:
        # Registering and logging in spend 2 of this address's auth budget.
        client.post(
            "/auth/register",
            json={"organization_name": "Measure Org", "email": email, "password": PASSWORD},
        )
        login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
        if login.status_code == 429:
            # The limiter doing its job: an earlier scenario spent this address's
            # auth budget. Measuring on a half-spent budget would be misleading.
            raise SystemExit(
                "Auth budget already spent from this address - restart the server "
                f"or wait {login.headers['Retry-After']} s, then run again."
            )
        login.raise_for_status()
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

        # Warm-up requests spend inference budget too; say so in the result.
        for _ in range(5):
            client.post("/classify", json={"text": "warm up request"})

        start = time.perf_counter()
        results = [
            timed_post(client, "/classify", {"text": f"I was charged twice for order {i}"})
            for i in range(count)
        ]
        report("/classify after 5 warm-up", results, time.perf_counter() - start)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["logins", "classify", "parallel-logins"])
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--email", default="measure@acme.example")
    args = parser.parse_args()

    if args.scenario == "logins":
        logins(args.count)
    elif args.scenario == "classify":
        classify(args.count, args.email)
    else:
        parallel_logins(args.count, args.threads)


if __name__ == "__main__":
    main()
