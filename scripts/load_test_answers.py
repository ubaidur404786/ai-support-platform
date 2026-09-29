"""What happens when several people ask for an answer at the same time?

Simulates 1, 2, 4 and 8 users. Each user is a thread that asks a few
questions one after another, waiting for each answer before the next - like a
person reading the reply. All questions asked at the same time are different
(llama.cpp reuses the work for a prompt it has just seen, so identical
questions would look faster than real traffic).

While the users wait, another thread calls GET /health twice a second, to see
whether the rest of the API stays responsive while the model is busy.

Reported per number of users:
    ok / busy / failed   answers returned / 503 "try again" / anything else
    P50, P95, max        seconds from sending a question to the whole answer
    answers per minute   throughput: how many answers the system finished
    /health P50, P95     milliseconds, measured during the same run

Start the API with the per-user rate limit switched off (otherwise one test
user is refused after 10 answers a minute), and DB_ECHO=false:
    RATE_LIMIT_ENABLED=false DB_ECHO=false uvicorn app.main:app
The evaluation knowledge base must be uploaded (scripts/evaluate_retrieval.py). Then:
    python scripts/load_test_answers.py
    python scripts/load_test_answers.py --users 1 2 4 8 --questions-per-user 3
"""

import argparse
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from evaluate_retrieval import BASE_URL, authenticate

QUESTIONS = Path("evaluation/rag_questions.jsonl")


def percentile(values: list[float], fraction: float) -> float:
    """The value below which `fraction` of the values fall (0.95 -> P95)."""
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]


def one_user(token: str, questions: list[str]) -> list[tuple[int, float]]:
    """Ask each question in turn; return (status code, seconds) per question."""
    results = []
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=600) as client:
        for question in questions:
            start = time.perf_counter()
            try:
                status = client.post("/answers", json={"question": question}).status_code
            except httpx.HTTPError:
                status = 0  # no response at all (timeout, connection refused)
            results.append((status, time.perf_counter() - start))
    return results


def watch_health(stop: threading.Event, timings_ms: list[float]) -> None:
    with httpx.Client(base_url=BASE_URL, timeout=60) as client:
        while not stop.is_set():
            start = time.perf_counter()
            client.get("/health")
            timings_ms.append((time.perf_counter() - start) * 1000)
            stop.wait(0.5)


def run_level(token: str, questions: list[str], users: int, per_user: int, offset: int) -> dict:
    # Give every user its own slice of the question list, so no two
    # simultaneous requests carry the same question.
    plans = []
    for user in range(users):
        start = offset + user * per_user
        plans.append([questions[(start + i) % len(questions)] for i in range(per_user)])

    health_ms: list[float] = []
    stop = threading.Event()
    watcher = threading.Thread(target=watch_health, args=(stop, health_ms))
    watcher.start()

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=users) as pool:
        per_user_results = list(pool.map(lambda plan: one_user(token, plan), plans))
    wall = time.perf_counter() - started

    stop.set()
    watcher.join()

    results = [result for user_results in per_user_results for result in user_results]
    ok = [seconds for status, seconds in results if status == 200]
    busy = [seconds for status, seconds in results if status == 503]
    return {
        "users": users,
        "requests": len(results),
        "ok": len(ok),
        "busy": len(busy),
        "failed": len(results) - len(ok) - len(busy),
        "p50": statistics.median(ok) if ok else None,
        "p95": percentile(ok, 0.95) if ok else None,
        "max": max(ok) if ok else None,
        "busy_max": max(busy) if busy else None,
        "answers_per_minute": len(ok) / wall * 60,
        "wall_seconds": wall,
        "health_p50_ms": statistics.median(health_ms),
        "health_p95_ms": percentile(health_ms, 0.95),
    }


def show(row: dict) -> None:
    def seconds(value):
        return f"{value:6.1f}" if value is not None else "     -"

    print(
        f"{row['users']:>5} | {row['ok']:>3} / {row['busy']:>3} / {row['failed']:>3} | "
        f"{seconds(row['p50'])} {seconds(row['p95'])} {seconds(row['max'])} | "
        f"{row['answers_per_minute']:6.1f} | "
        f"{row['health_p50_ms']:7.0f} {row['health_p95_ms']:7.0f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--questions-per-user", type=int, default=3)
    parser.add_argument("--output", help="save the rows as JSON")
    args = parser.parse_args()

    questions = [
        q["question"]
        for q in map(json.loads, QUESTIONS.read_text(encoding="utf-8").splitlines())
        if q["answerable"]
    ]
    with httpx.Client(base_url=BASE_URL, timeout=600) as client:
        authenticate(client)
        token = client.headers["Authorization"].removeprefix("Bearer ")
        # Untimed: the first answer loads the models.
        client.post("/answers", json={"question": "Which browsers are supported?"})

    print("users |  ok / busy / failed |  P50    P95    max (s) | ans/min | /health P50 P95 (ms)")
    rows, offset = [], 0
    for users in args.users:
        row = run_level(token, questions, users, args.questions_per_user, offset)
        offset += users * args.questions_per_user
        rows.append(row)
        show(row)

    if args.output:
        Path(args.output).write_text(json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
