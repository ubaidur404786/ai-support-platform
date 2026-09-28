"""Choose the relevance threshold from data, not by guessing.

Semantic search always returns its closest chunks, even for "What is the
capital of France?". Before a model writes answers from those chunks, the system
must be able to say "nothing relevant was found". The rule is simple: if the
best chunk's score (cosine similarity) is below a threshold, do not answer.

This script shows where the threshold should be. For each question in
evaluation/rag_questions.jsonl it records the best score, then tries thresholds
from 0.20 to 0.60 and counts:

    answered   answerable questions whose best chunk is above the threshold
    refused    unanswerable questions whose best chunk is below it

The questions are split in two halves: the threshold is CHOSEN on "tune" and
then CHECKED on "test", so the reported result is not measured on the same
questions that picked it.

Needs the evaluation knowledge base uploaded and embedded in one organisation
(scripts/evaluate_retrieval.py does the upload). From the repository root:
    DB_ECHO=false python scripts/choose_relevance_threshold.py --organization-id 6
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.auth.models  # noqa: E402,F401
import app.tickets.models  # noqa: E402,F401
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.documents.embeddings import embed_texts  # noqa: E402
from app.documents.repository import PostgresDocumentRepository  # noqa: E402

QUESTIONS = Path("evaluation/rag_questions.jsonl")
THRESHOLDS = [round(0.20 + 0.05 * i, 2) for i in range(9)]  # 0.20, 0.25 ... 0.60


def best_scores(organization_id: int, questions: list[dict]) -> list[float]:
    vectors = embed_texts([q["question"] for q in questions])
    scores = []
    with SessionLocal() as session:
        repository = PostgresDocumentRepository(session)
        for vector in vectors:
            hits = repository.semantic_search(organization_id, vector, settings.embedding_model, 1)
            scores.append(hits[0].rank if hits else 0.0)
    return scores


def rates(items: list[tuple[dict, float]], threshold: float) -> tuple[float, float]:
    """(share of answerable questions answered, share of unanswerable refused)."""
    answerable = [score for q, score in items if q["answerable"]]
    unanswerable = [score for q, score in items if not q["answerable"]]
    answered = sum(score >= threshold for score in answerable) / len(answerable)
    refused = sum(score < threshold for score in unanswerable) / len(unanswerable)
    return answered, refused


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    organization_id = parser.parse_args().organization_id

    questions = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines()]
    scored = list(zip(questions, best_scores(organization_id, questions)))

    print("Best score per question (sorted):")
    for q, score in sorted(scored, key=lambda item: item[1]):
        kind = "answerable  " if q["answerable"] else "UNANSWERABLE"
        print(f"  {score:.3f}  {kind}  {q['question']}")

    tune = [item for item in scored if item[0]["split"] == "tune"]
    test = [item for item in scored if item[0]["split"] == "test"]
    print(f"\n{'threshold':>9}  {'tune: answered':>15} {'refused':>8}  {'test: answered':>15} {'refused':>8}")
    best = None
    for threshold in THRESHOLDS:
        tune_answered, tune_refused = rates(tune, threshold)
        test_answered, test_refused = rates(test, threshold)
        print(f"{threshold:>9.2f}  {tune_answered:>15.2f} {tune_refused:>8.2f}  "
              f"{test_answered:>15.2f} {test_refused:>8.2f}")
        # Balanced accuracy on the tune half: both kinds of mistake count equally.
        balanced = (tune_answered + tune_refused) / 2
        if best is None or balanced > best[1]:
            best = (threshold, balanced)
    print(f"\nBest on the tune half: {best[0]:.2f} (balanced accuracy {best[1]:.2f})")


if __name__ == "__main__":
    main()
