"""Load test: N simulated users send requests for a few seconds.

Each user sends a request, waits for the answer, then sends the next one
(no pause). So more users = more requests in flight at the same time.

    python loadtest/loadtest.py --path /api/tickets --users 1,10,50 --seconds 15

Prints one line per user count: requests per second and latency
percentiles (P50 = half the requests were faster than this; P95 = 95%).
"""

import argparse
import asyncio
import random
import statistics
import time
from collections import Counter

import httpx


async def one_user(client, url_prefix, max_id, stop_at, latencies, gateways, errors):
    while time.perf_counter() < stop_at:
        url = f"{url_prefix}/{random.randint(1, max_id)}"
        started = time.perf_counter()
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            errors.append(1)
            continue
        latencies.append((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            errors.append(1)
        gateways[response.headers.get("x-gateway", "?")] += 1


async def run_level(base_url, path, max_id, users, seconds):
    latencies, errors, gateways = [], [], Counter()
    limits = httpx.Limits(max_connections=users)
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0, limits=limits) as client:
        stop_at = time.perf_counter() + seconds
        await asyncio.gather(*[
            one_user(client, path, max_id, stop_at, latencies, gateways, errors)
            for _ in range(users)
        ])

    latencies.sort()
    pct = lambda p: latencies[min(len(latencies) - 1, int(len(latencies) * p))]
    print(
        f"users={users:>4}  req/s={len(latencies) / seconds:>7.1f}  "
        f"p50={pct(0.50):>7.1f} ms  p95={pct(0.95):>7.1f} ms  p99={pct(0.99):>7.1f} ms  "
        f"errors={len(errors)}  gateways={dict(gateways)}",
        flush=True,
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://nginx")
    parser.add_argument("--path", default="/api/tickets")
    parser.add_argument("--max-id", type=int, default=1000)
    parser.add_argument("--users", default="1,10,50,100")
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args()

    print(f"{args.url}{args.path}  ({args.seconds} s per level)")
    for users in [int(u) for u in args.users.split(",")]:
        await run_level(args.url, args.path, args.max_id, users, args.seconds)
        await asyncio.sleep(2)  # let queues drain between levels


if __name__ == "__main__":
    asyncio.run(main())
