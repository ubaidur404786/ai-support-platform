"""Load test: N simulated users send requests for a few seconds.

Each user sends a request, waits for the answer, then sends the next one
(no pause). So more users = more requests in flight at the same time.

    python loadtest/loadtest.py --path /api/tickets --users 1,10,50 --label step1

Prints one line per user count: requests per second and latency
percentiles (P50 = half the requests were faster than this; P95 = 95%).
With --label, also saves the numbers to results/<label>.json for the plots.

The users are spread over several processes (--processes), because one
Python process can only send ~1,000 requests/s; the tester must never be
the slowest part of the test.
"""

import argparse
import asyncio
import json
import multiprocessing
import random
import time
from collections import Counter

import httpx


async def one_user(client, args, stop_at, latencies, gateways, errors):
    while time.perf_counter() < stop_at:
        user_id = random.randint(1, 1000)
        started = time.perf_counter()
        try:
            if args.method == "POST":
                response = await client.post(args.path, json={"user_id": user_id, "text": "my order is late"})
            elif args.no_id:
                response = await client.get(args.path)
            else:
                response = await client.get(f"{args.path}/{random.randint(1, args.max_id)}")
        except httpx.HTTPError:
            errors.append(1)
            continue
        latencies.append((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            errors.append(1)
        gateways[response.headers.get("x-gateway", "?")] += 1


async def run_users(args, users, stop_at):
    latencies, errors, gateways = [], [], Counter()
    limits = httpx.Limits(max_connections=users)
    async with httpx.AsyncClient(base_url=args.url, timeout=60.0, limits=limits) as client:
        await asyncio.gather(*[
            one_user(client, args, stop_at, latencies, gateways, errors) for _ in range(users)
        ])
    return latencies, len(errors), gateways


def worker(args, users, stop_at, queue):
    queue.put(asyncio.run(run_users(args, users, stop_at)))


def run_level(args, users):
    processes = min(args.processes, users)
    # All processes stop at the same moment.
    stop_at = time.perf_counter() + args.seconds
    queue = multiprocessing.Queue()
    jobs = []
    for i in range(processes):
        share = users // processes + (1 if i < users % processes else 0)
        job = multiprocessing.Process(target=worker, args=(args, share, stop_at, queue))
        job.start()
        jobs.append(job)

    latencies, errors, gateways = [], 0, Counter()
    for _ in jobs:
        lat, err, gw = queue.get()
        latencies += lat
        errors += err
        gateways.update(gw)
    for job in jobs:
        job.join()

    latencies.sort()
    pct = lambda p: latencies[min(len(latencies) - 1, int(len(latencies) * p))] if latencies else 0
    row = {
        "users": users,
        "rps": round(len(latencies) / args.seconds, 1),
        "p50": round(pct(0.50), 1),
        "p95": round(pct(0.95), 1),
        "p99": round(pct(0.99), 1),
        "errors": errors,
        "gateways": dict(gateways),
    }
    print(
        f"users={users:>4}  req/s={row['rps']:>7.1f}  p50={row['p50']:>7.1f} ms  "
        f"p95={row['p95']:>7.1f} ms  p99={row['p99']:>7.1f} ms  errors={errors}  gateways={row['gateways']}",
        flush=True,
    )
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://nginx")
    parser.add_argument("--path", default="/api/tickets")
    parser.add_argument("--method", default="GET", choices=["GET", "POST"])
    parser.add_argument("--no-id", action="store_true", help="call --path as it is (no /<id>)")
    parser.add_argument("--max-id", type=int, default=200000)
    parser.add_argument("--users", default="1,10,50,100")
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--processes", type=int, default=4)
    parser.add_argument("--label", help="save results to results/<label>.json")
    args = parser.parse_args()

    print(f"{args.method} {args.url}{args.path}  ({args.seconds} s per level)", flush=True)
    rows = []
    for users in [int(u) for u in args.users.split(",")]:
        rows.append(run_level(args, users))
        time.sleep(2)  # let queues drain between levels

    if args.label:
        with open(f"results/{args.label}.json", "w") as f:
            json.dump({"label": args.label, "method": args.method, "path": args.path, "rows": rows}, f, indent=2)


if __name__ == "__main__":
    main()
