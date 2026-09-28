"""Measure how well search finds the right document for a question.

Uploads the evaluation knowledge base (evaluation/knowledge_base/*.md) into its
own organisation through the real API, asks every question in
evaluation/retrieval_questions.jsonl, and reports:

    hit@k  - fraction of questions whose expected document is among the first
             k documents returned (each question has exactly one right answer,
             so this is also recall@k)
    MRR    - mean reciprocal rank: 1 if the right document is first, 1/2 if
             second, ... 0 if it is not in the top 5. Rewards ranking it higher.
    empty  - fraction of questions that returned nothing at all

split by question type: "lexical" (shares key words with the article) and
"paraphrase" (same meaning, different words).

Start the server and the worker (python -m app.worker) first - uploads are
processed in the background since v7. Then, from the repository root:
    python scripts/evaluate_retrieval.py
    python scripts/evaluate_retrieval.py --output evaluation/results/v6-keyword-search.json
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx

BASE_URL = "http://127.0.0.1:8000"
KNOWLEDGE_BASE = Path("evaluation/knowledge_base")
QUESTIONS = Path("evaluation/retrieval_questions.jsonl")
EMAIL = "evaluation@eval.example"
PASSWORD = "evaluation-password-long-enough"
TOP_K = 5


def authenticate(client: httpx.Client) -> None:
    client.post(
        "/auth/register",
        json={"organization_name": "Retrieval Evaluation", "email": EMAIL, "password": PASSWORD},
    )  # 409 on a second run is fine: the account already exists
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    response.raise_for_status()
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def upload_knowledge_base(client: httpx.Client) -> dict[int, str]:
    """Upload every article; return document id -> file name."""
    for path in sorted(KNOWLEDGE_BASE.glob("*.md")):
        response = client.post(
            "/documents", files={"file": (path.name, path.read_bytes(), "text/markdown")}
        )
        # 409 = uploaded by an earlier run. Anything else is a real failure.
        if response.status_code not in (202, 409):
            raise SystemExit(f"Upload of {path.name} failed: {response.status_code} {response.text}")

    # Wait for the worker: a document is only searchable once it is "ready".
    deadline = time.monotonic() + 120
    while True:
        listing = client.get("/documents", params={"limit": 200}).json()
        statuses = {item["status"] for item in listing["items"]}
        if statuses <= {"ready", "failed"}:
            break
        if time.monotonic() > deadline:
            raise SystemExit("Documents still not processed after 120 s - is the worker running?")
        time.sleep(0.5)
    return {item["id"]: item["filename"] for item in listing["items"]}


def ranked_documents(results: list[dict], id_to_file: dict[int, str]) -> list[str]:
    """Distinct documents in the order their best chunk appeared."""
    seen: list[str] = []
    for result in results:
        name = id_to_file[result["document_id"]]
        if name not in seen:
            seen.append(name)
    return seen


def evaluate(client: httpx.Client, questions: list[dict], id_to_file: dict[int, str], match: str) -> dict:
    rows = []
    latencies = []
    for item in questions:
        start = time.perf_counter()
        response = client.get(
            "/documents/search",
            params={"q": item["question"], "limit": TOP_K * 3, "match": match},
        )
        latencies.append((time.perf_counter() - start) * 1000)
        response.raise_for_status()
        documents = ranked_documents(response.json()["results"], id_to_file)[:TOP_K]
        rank = documents.index(item["expected_document"]) + 1 if item["expected_document"] in documents else None
        rows.append({**item, "returned": documents, "rank": rank})

    def summarise(subset: list[dict]) -> dict:
        n = len(subset)
        return {
            "questions": n,
            "hit@1": sum(r["rank"] == 1 for r in subset) / n,
            "hit@3": sum(r["rank"] is not None and r["rank"] <= 3 for r in subset) / n,
            "hit@5": sum(r["rank"] is not None for r in subset) / n,
            "mrr": sum(1 / r["rank"] for r in subset if r["rank"]) / n,
            "empty": sum(not r["returned"] for r in subset) / n,
        }

    return {
        "match": match,
        "overall": summarise(rows),
        "lexical": summarise([r for r in rows if r["type"] == "lexical"]),
        "paraphrase": summarise([r for r in rows if r["type"] == "paraphrase"]),
        "latency_ms_p50": statistics.median(latencies),
        "questions": rows,
    }


def print_report(report: dict) -> None:
    print(f"\nmatch={report['match']}   search latency P50 {report['latency_ms_p50']:.1f} ms")
    print(f"  {'':<11}{'n':>3}  {'hit@1':>6} {'hit@3':>6} {'hit@5':>6} {'MRR':>6} {'empty':>6}")
    for part in ("overall", "lexical", "paraphrase"):
        s = report[part]
        print(
            f"  {part:<11}{s['questions']:>3}  {s['hit@1']:>6.2f} {s['hit@3']:>6.2f} "
            f"{s['hit@5']:>6.2f} {s['mrr']:>6.2f} {s['empty']:>6.2f}"
        )
    misses = [r for r in report["questions"] if r["rank"] is None or r["rank"] > 3]
    if misses:
        print("  not in top 3:")
        for r in misses:
            found = ", ".join(r["returned"][:3]) or "nothing"
            print(f"    [{r['type']}] {r['question']!r} -> expected {r['expected_document']}, got {found}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Write the full results as JSON.")
    args = parser.parse_args()

    questions = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line]
    with httpx.Client(base_url=BASE_URL, timeout=60) as client:
        authenticate(client)
        id_to_file = upload_knowledge_base(client)
        reports = [evaluate(client, questions, id_to_file, match) for match in ("any", "all")]

    for report in reports:
        print_report(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(reports, indent=2), encoding="utf-8")
        print(f"\nwritten to {args.output}")


if __name__ == "__main__":
    main()
