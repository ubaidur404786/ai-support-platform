"""Send requests to the running API and report latency percentiles and throughput.

Start the server first (uvicorn app.main:app), then run from the repository root:
    python scripts/measure_latency.py
    python scripts/measure_latency.py --requests 1000 --concurrency 10
"""

import argparse
import asyncio
import statistics
import time

# httpx is an HTTP client (like "requests") that also supports async, so we can
# keep several requests in flight at once from a single script.
import httpx

SAMPLE_TEXTS = [
    "I was charged twice for my subscription this month",
    "The app crashes every time I open the reports page",
    "I forgot my password and the reset email never arrives",
    "It would be great to have a dark mode in the app",
]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[index]


async def send_one(client: httpx.AsyncClient, url: str, text: str) -> tuple[float, bool]:
    started = time.perf_counter()
    try:
        response = await client.post(url, json={"text": text}, timeout=10.0)
        ok = response.status_code == 200
    except httpx.HTTPError:
        ok = False
    return (time.perf_counter() - started) * 1000, ok  # milliseconds


async def run(url: str, total: int, concurrency: int) -> None:
    # A semaphore limits how many requests are in flight at the same time.
    semaphore = asyncio.Semaphore(concurrency)

    async def limited(client: httpx.AsyncClient, index: int) -> tuple[float, bool]:
        async with semaphore:
            return await send_one(client, url, SAMPLE_TEXTS[index % len(SAMPLE_TEXTS)])

    async with httpx.AsyncClient() as client:
        # Warm-up: the first request pays one-time costs (connection setup, imports).
        await send_one(client, url, SAMPLE_TEXTS[0])

        started = time.perf_counter()
        results = await asyncio.gather(*(limited(client, i) for i in range(total)))
        elapsed = time.perf_counter() - started

    latencies = [ms for ms, _ in results]
    failures = sum(1 for _, ok in results if not ok)

    print(f"requests     : {total}  (concurrency {concurrency})")
    print(f"failures     : {failures}  ({failures / total:.1%})")
    print(f"total time   : {elapsed:.2f} s")
    print(f"throughput   : {total / elapsed:.1f} req/s")
    print(f"latency mean : {statistics.mean(latencies):.1f} ms")
    print(f"latency P50  : {percentile(latencies, 0.50):.1f} ms")
    print(f"latency P95  : {percentile(latencies, 0.95):.1f} ms")
    print(f"latency P99  : {percentile(latencies, 0.99):.1f} ms")
    print(f"latency max  : {max(latencies):.1f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/classify")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.requests, args.concurrency))


if __name__ == "__main__":
    main()