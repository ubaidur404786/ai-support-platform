"""Does combining keyword and semantic search find better chunks? (Measured in v11: no.)

Hybrid retrieval runs both searches and merges the two ranked lists with
reciprocal rank fusion (RRF): every chunk scores 1 / (60 + its rank) in each
list it appears in, and the scores are added. A chunk near the top of both
lists wins. It needs no score calibration, which is why it is the usual
first try.

This script compares, on the 30 retrieval questions:
    semantic      v8/v9 semantic search alone (what /answers uses)
    hybrid any    RRF of semantic + keyword search matching ANY query word
    hybrid all    RRF of semantic + keyword search matching ALL query words

reporting, per method, whether the expected article is ranked 1st (hit@1),
in the top 3 (hit@3), and the mean reciprocal rank (MRR).

From the repository root, against the organisation that holds the evaluation
knowledge base:
    DB_ECHO=false python scripts/compare_hybrid_retrieval.py --organization-id 6
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.auth.models  # noqa: E402,F401
import app.tickets.models  # noqa: E402,F401
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.documents.models import Document  # noqa: E402
from app.documents.repository import PostgresDocumentRepository  # noqa: E402
from app.documents.service import DocumentService  # noqa: E402

QUESTIONS = Path("evaluation/retrieval_questions.jsonl")
CANDIDATES = 20  # chunks taken from each search before merging


def fuse(*ranked_lists) -> list:
    """Reciprocal rank fusion: merge ranked lists of chunks into one."""
    scores = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):
            key = (hit.document_id, hit.chunk_index)
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return sorted(scores, key=lambda key: scores[key], reverse=True)


def rank_of(expected: str, chunk_keys: list, filenames: dict[int, str]) -> int | None:
    """Position of the expected article among the distinct articles returned."""
    articles = []
    for document_id, _ in chunk_keys:
        if filenames[document_id] not in articles:
            articles.append(filenames[document_id])
    return articles.index(expected) + 1 if expected in articles else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    organization_id = parser.parse_args().organization_id
    questions = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines()]

    ranks = {"semantic": [], "hybrid any": [], "hybrid all": []}
    with SessionLocal() as session:
        filenames = {
            d.id: d.filename
            for d in session.scalars(select(Document).where(Document.organization_id == organization_id))
        }
        search = DocumentService(
            PostgresDocumentRepository(session),
            max_document_bytes=0,
            max_pending_documents=0,
            max_page_size=1,
            max_search_results=CANDIDATES,
            embedding_model=settings.embedding_model,
        ).search
        for q in questions:
            text = q["question"]
            semantic = search(organization_id, text, limit=CANDIDATES, mode="semantic")
            any_words = search(organization_id, text, limit=CANDIDATES, match="any")
            all_words = search(organization_id, text, limit=CANDIDATES, match="all")
            runs = {
                "semantic": [(h.document_id, h.chunk_index) for h in semantic],
                "hybrid any": fuse(semantic, any_words),
                "hybrid all": fuse(semantic, all_words),
            }
            for method, keys in runs.items():
                ranks[method].append((q["type"], rank_of(q["expected_document"], keys, filenames)))

    print(f"{'method':<11} {'part':<11} {'hit@1':>6} {'hit@3':>6} {'MRR':>6}")
    for method, rows in ranks.items():
        for part in ("overall", "lexical", "paraphrase"):
            r = [rank for kind, rank in rows if part in ("overall", kind)]
            print(f"{method:<11} {part:<11} "
                  f"{statistics.mean([x == 1 for x in r]):>6.2f} "
                  f"{statistics.mean([x is not None and x <= 3 for x in r]):>6.2f} "
                  f"{statistics.mean([1 / x if x else 0 for x in r]):>6.2f}")


if __name__ == "__main__":
    main()
