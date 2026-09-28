"""Evaluate generated answers on questions with known answers - and without.

Runs every question in evaluation/rag_questions.jsonl through POST /answers and
reports, separately for the two kinds of question:

Answerable (30) - the knowledge base contains the answer:
    answered     the API gave an answer (not "not found")
    correct      the answer contains one of the question's expected facts
                 ("five to ten business days", "25 MB", ...). A simple string
                 check: it can miss a correct answer worded differently, and it
                 cannot see a wrong sentence next to a right one. Read the
                 answers in the output file too.
    cited        the article that holds the answer was among the sources

Unanswerable (20) - the knowledge base does not contain the answer:
    refused      the API said "not found" - the right behaviour
    hallucinated the API gave an answer anyway - the failure RAG must avoid

Plus latency, split by whether the model was called.

Start the API and the worker first (DB_ECHO=false), then:
    python scripts/evaluate_rag.py --output evaluation/results/v10-rag.json
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx

# Same account and knowledge base as the retrieval evaluation (v6, v8).
from evaluate_retrieval import BASE_URL, authenticate, upload_knowledge_base

QUESTIONS = Path("evaluation/rag_questions.jsonl")


def ask(client: httpx.Client, question: str) -> tuple[dict, float]:
    """POST /answers; wait and retry when the per-user answer budget is used up."""
    while True:
        start = time.perf_counter()
        response = client.post("/answers", json={"question": question})
        seconds = time.perf_counter() - start
        if response.status_code == 429:
            time.sleep(float(response.headers.get("Retry-After", "5")))
            continue
        response.raise_for_status()
        return response.json(), seconds


def share(items: list[bool]) -> float:
    return round(sum(items) / len(items), 2) if items else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Write every question and answer as JSON.")
    args = parser.parse_args()

    questions = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line]
    rows = []
    with httpx.Client(base_url=BASE_URL, timeout=300) as client:
        authenticate(client)
        id_to_file = upload_knowledge_base(client)
        # Untimed: the API loads both models on the first answer.
        ask(client, "How long does a refund take?")

        for number, q in enumerate(questions, start=1):
            body, seconds = ask(client, q["question"])
            answer = body["answer"].lower()
            cited = [id_to_file.get(s["document_id"]) for s in body["sources"]]
            rows.append({
                **q,
                "answer": body["answer"],
                "answered": body["answered"],
                "reason": body["reason"],
                "sources": cited,
                "correct": body["answered"] and any(f.lower() in answer for f in q["expected_facts"]),
                "cited": q["expected_document"] in cited,
                "seconds": round(seconds, 2),
            })
            print(f"{number:>2}/{len(questions)} {seconds:5.1f}s {body['reason']:<20} {q['question']}")

    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    model_called = [r["seconds"] for r in rows if r["reason"] != "no_relevant_sources"]
    not_called = [r["seconds"] for r in rows if r["reason"] == "no_relevant_sources"]
    summary = {
        "answerable": {
            "questions": len(answerable),
            "answered": share([r["answered"] for r in answerable]),
            "correct": share([r["correct"] for r in answerable]),
            "cited": share([r["cited"] for r in answerable]),
        },
        "unanswerable": {
            "questions": len(unanswerable),
            "refused": share([not r["answered"] for r in unanswerable]),
            "refused_by_threshold": share([r["reason"] == "no_relevant_sources" for r in unanswerable]),
            "refused_by_model": share([r["reason"] == "model_declined" for r in unanswerable]),
            "hallucinated": share([r["answered"] for r in unanswerable]),
        },
        "latency_seconds": {
            "model_called_p50": round(statistics.median(model_called), 2) if model_called else None,
            "model_called_max": round(max(model_called), 2) if model_called else None,
            "not_called_p50": round(statistics.median(not_called), 2) if not_called else None,
        },
    }

    print("\n" + json.dumps(summary, indent=2))
    print("\nAnswerable but not correct:")
    for r in answerable:
        if not r["correct"]:
            print(f"  [{r['reason']}] {r['question']!r} -> {r['answer'][:150]!r}")
    print("Unanswerable but answered (hallucinated):")
    for r in unanswerable:
        if r["answered"]:
            print(f"  {r['question']!r} -> {r['answer'][:150]!r}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"summary": summary, "questions": rows}, indent=2), encoding="utf-8")
        print(f"\nwritten to {args.output}")


if __name__ == "__main__":
    main()
