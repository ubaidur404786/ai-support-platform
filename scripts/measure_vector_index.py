"""Compare the HNSW index with exact search, on the same data and the same questions.

The index is fast because it does NOT look at every chunk - which also means it
can miss one of the true nearest chunks. This script measures both sides:

    speed      P50 time of one search, run through the real repository code
    recall@5   of the 5 truly nearest chunks, how many the index also returned
               (1.00 = the index gave exactly the exact answer)
    nearest    how often the single closest chunk was among the index's 5

"Exact" is the same query with the index switched off, so PostgreSQL compares
the question with every chunk. The index is tried with hnsw.ef_search = 40
(pgvector's default: how many candidates the search keeps while walking the
graph), 100 and 200. More candidates = better recall, slower search.

Two kinds of question vectors, 50 of each:
    random   points with no close chunk at all - the hardest case for an index
    near     a stored chunk plus a little noise - a question with a clear answer

Run against a database with a large organisation, e.g. the one filled by
scripts/measure_semantic_search.py:
    DB_ECHO=false python scripts/measure_vector_index.py --organization-id 3
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from sqlalchemy import func, select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.auth.models  # noqa: E402,F401
import app.tickets.models  # noqa: E402,F401
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.documents.models import DocumentChunk  # noqa: E402
from app.documents import repository  # noqa: E402
from app.documents.repository import PostgresDocumentRepository  # noqa: E402

QUESTIONS_PER_KIND = 50
LIMIT = 5
EF_SEARCH_VALUES = [40, 100, 200]


def unit(vectors: np.ndarray) -> np.ndarray:
    return (vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)).astype(np.float32)


def make_questions(organization_id: int) -> dict[str, list[np.ndarray]]:
    rng = np.random.default_rng(seed=9)
    with SessionLocal() as session:
        stored = session.scalars(
            select(DocumentChunk.embedding)
            .where(DocumentChunk.organization_id == organization_id)
            .where(DocumentChunk.embedding.is_not(None))
            .order_by(func.random())
            .limit(QUESTIONS_PER_KIND)
        ).all()
    near = [unit(v + 0.02 * rng.standard_normal(384)) for v in stored]
    random_points = list(unit(rng.standard_normal((QUESTIONS_PER_KIND, 384))))
    return {"random": random_points, "near": near}


def search(organization_id: int, question: np.ndarray, setting: str) -> tuple[list, float, bool]:
    """One search through the repository, after one SET LOCAL (or none).

    Returns the results as (document_id, chunk_index) pairs, the time in ms,
    and whether PostgreSQL really used the HNSW index.
    """
    with SessionLocal() as session:
        if setting:
            session.execute(text(setting))
        start = time.perf_counter()
        hits = PostgresDocumentRepository(session).semantic_search(
            organization_id, question, settings.embedding_model, LIMIT
        )
        ms = (time.perf_counter() - start) * 1000
        scans = session.scalar(
            text("SELECT pg_stat_get_xact_numscans('ix_document_chunks_embedding'::regclass)")
        )
    return [(hit.document_id, hit.chunk_index) for hit in hits], ms, scans > 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    organization_id = parser.parse_args().organization_id

    with SessionLocal() as session:
        chunks = session.scalar(
            select(func.count()).where(DocumentChunk.organization_id == organization_id)
        )
    print(f"Organisation {organization_id}: {chunks:,} chunks, top {LIMIT}\n")

    # name -> (SQL run before the search, ef_search the repository will use)
    methods = {"exact (index off)": ("SET LOCAL enable_indexscan = off", repository.HNSW_EF_SEARCH)}
    for ef in EF_SEARCH_VALUES:
        methods[f"index, ef_search={ef}"] = ("", ef)

    for kind, questions in make_questions(organization_id).items():
        print(f"{kind} questions (n={len(questions)})")
        print(f"  {'method':<22} {'P50 ms':>8} {'P95 ms':>8} {'recall@5':>9} {'nearest':>8}  index used")
        truth = []  # the exact top 5 of each question, in order
        for name, (setting, ef) in methods.items():
            # The repository sets hnsw.ef_search from this constant on every search.
            repository.HNSW_EF_SEARCH = ef
            search(organization_id, questions[0], setting)  # warm-up, untimed
            timings, recalls, nearest, used = [], [], [], set()
            for i, question in enumerate(questions):
                found, ms, index_used = search(organization_id, question, setting)
                timings.append(ms)
                used.add(index_used)
                if name.startswith("exact"):
                    truth.append(found)
                else:
                    recalls.append(len(set(truth[i]) & set(found)) / len(truth[i]))
                    nearest.append(truth[i][0] in found)
            timings.sort()
            recall = f"{statistics.mean(recalls):.2f}" if recalls else "truth"
            top = f"{statistics.mean(nearest):.2f}" if nearest else "truth"
            print(f"  {name:<22} {statistics.median(timings):>8.1f} "
                  f"{timings[int(len(timings) * 0.95) - 1]:>8.1f} {recall:>9} {top:>8}  "
                  f"{'/'.join(sorted('yes' if u else 'no' for u in used))}")
        print()


if __name__ == "__main__":
    main()
