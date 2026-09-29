"""How long until the user sees something: whole answers vs streamed answers.

For each answerable question in evaluation/rag_questions.jsonl, asks twice:

    POST /answers          time until the whole answer arrives
    POST /answers/stream   time until the sources line, the first piece of
                           text, and the final "done" line

The number that changes what a person experiences is "first text": the moment
the answer starts to appear. The total time barely changes - the model writes
the same words at the same speed either way.

Start the API first (DB_ECHO=false), with the evaluation knowledge base
uploaded (scripts/evaluate_retrieval.py does that), then:
    python scripts/measure_answer_streaming.py
"""

import json
import statistics
import time
from pathlib import Path

import httpx

from evaluate_retrieval import BASE_URL, authenticate

QUESTIONS = Path("evaluation/rag_questions.jsonl")


def post(client: httpx.Client, path: str, question: str) -> httpx.Response:
    """Send the question; wait and retry when the per-user answer budget is used up."""
    while True:
        response = client.post(path, json={"question": question})
        if response.status_code != 429:
            response.raise_for_status()
            return response
        time.sleep(float(response.headers.get("Retry-After", "5")))


def timed_stream(client: httpx.Client, question: str) -> dict:
    """Stream one answer, noting when each kind of line arrived (seconds)."""
    while True:
        start = time.perf_counter()
        seen = {}
        with client.stream("POST", "/answers/stream", json={"question": question}) as response:
            if response.status_code == 429:
                time.sleep(float(response.headers.get("Retry-After", "5")))
                continue
            response.raise_for_status()
            # iter_lines() hands over each line as soon as it has arrived.
            for line in response.iter_lines():
                event = json.loads(line)
                seen.setdefault(event["type"], time.perf_counter() - start)
                if event["type"] == "done":
                    seen["answer"] = event["answer"]
        return seen


def p50(values: list[float]) -> str:
    return f"{statistics.median(values):.2f} s" if values else "-"


def main() -> None:
    questions = [
        q["question"]
        for q in map(json.loads, QUESTIONS.read_text(encoding="utf-8").splitlines())
        if q["answerable"]
    ]
    whole, answers = [], {}
    sources, first_text, done, same = [], [], [], []
    with httpx.Client(base_url=BASE_URL, timeout=300) as client:
        authenticate(client)
        post(client, "/answers", "Which browsers are supported?")  # untimed: loads the models

        # Two separate passes, never the same question twice in a row: llama.cpp
        # reuses the work for a prompt it has just seen, so asking the same
        # question again at once would make the second request look far faster
        # than it is (measured: 2.1 s instead of ~7 s).
        print("Pass 1: POST /answers")
        for question in questions:
            start = time.perf_counter()
            answers[question] = post(client, "/answers", question).json()["answer"]
            whole.append(time.perf_counter() - start)

        print("Pass 2: POST /answers/stream")
        for number, question in enumerate(questions, start=1):
            seen = timed_stream(client, question)
            if "text" not in seen:
                # Refused (by the threshold or the model): nothing to stream.
                print(f"{number:>2} refused, skipped: {question}")
                continue
            sources.append(seen["sources"])
            first_text.append(seen["text"])
            done.append(seen["done"])
            same.append(seen["answer"] == answers[question])
            print(f"{number:>2} first text {seen['text']:5.2f} s, done {seen['done']:5.1f} s | {question}")

    print(f"\nAnswered questions: {len(first_text)} of {len(questions)}")
    print(f"POST /answers, whole answer        P50 {p50(whole)}")
    print(f"POST /answers/stream, sources line P50 {p50(sources)}")
    print(f"POST /answers/stream, first text   P50 {p50(first_text)}  (max {max(first_text):.2f} s)")
    print(f"POST /answers/stream, done         P50 {p50(done)}")
    print(f"Streamed answer identical to the whole one: {sum(same)} of {len(same)}")


if __name__ == "__main__":
    main()
