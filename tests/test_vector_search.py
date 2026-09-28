"""Tests for semantic search through the pgvector index (v9).

The API tests in test_documents_api.py use a handful of chunks. On a table that
small PostgreSQL never bothers with the HNSW index - reading every row is
cheaper - so those tests pass whether the index works or not. These tests make
the database use the index, and check two things that only show up then:

    1. our query is written so the index CAN be used at all;
    2. a small organisation still gets its results when a large one shares the
       index (the filtering problem, ADR-025).

The vectors are random, not made by the model: the tests are about the
database, and random vectors are instant to create.
"""

import numpy as np
import pytest
from sqlalchemy import text

from app.auth.models import Organization, User
from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.models import READY, Document, DocumentChunk
from app.documents.repository import PostgresDocumentRepository

SMALL_ORG, LARGE_ORG = 1, 2


def unit(vectors: np.ndarray) -> np.ndarray:
    """Scale every row to length 1, like the real model does."""
    return vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)


@pytest.fixture
def question(clean_database) -> np.ndarray:
    """A question vector, and two organisations sharing one index.

    The large organisation has 300 chunks, all CLOSE to the question. The small
    one has 3 chunks, all FAR from it. Walking the index from the question
    therefore meets only the large organisation's chunks first.
    """
    rng = np.random.default_rng(seed=7)
    question = unit(rng.standard_normal(384)).astype(np.float32)
    chunks_per_org = {
        LARGE_ORG: unit(question + 0.03 * rng.standard_normal((300, 384))),
        SMALL_ORG: unit(rng.standard_normal((3, 384))),
    }
    with SessionLocal() as session:
        for org_id, vectors in chunks_per_org.items():
            session.add(Organization(id=org_id, name=f"Org {org_id}"))
            session.add(
                User(id=org_id, organization_id=org_id, email=f"{org_id}@example.com", password_hash="x")
            )
            session.flush()
            document = Document(
                organization_id=org_id,
                uploaded_by_user_id=org_id,
                title=f"Org {org_id} handbook",
                filename="handbook.txt",
                content_type="text",
                size_bytes=0,
                sha256="0" * 64,
                status=READY,
                chunk_count=len(vectors),
                embedding_model=settings.embedding_model,
            )
            session.add(document)
            session.flush()
            session.add_all(
                DocumentChunk(
                    document_id=document.id,
                    organization_id=org_id,
                    chunk_index=i,
                    text=f"chunk {i}",
                    start_char=0,
                    end_char=1,
                    embedding=vector,
                )
                for i, vector in enumerate(vectors)
            )
        session.commit()
        # Fresh table statistics, so the planner knows how many rows exist.
        session.execute(text("ANALYZE document_chunks"))
        session.commit()
    return question


def search_through_the_index(org_id: int, question: np.ndarray):
    """Run the real repository search, forcing PostgreSQL to use the index.

    enable_sort = off makes "read the rows and sort them" look very expensive
    to the planner, so it picks the index, which returns rows already in order.
    Returns the results and how many times the HNSW index was scanned.
    """
    with SessionLocal() as session:
        session.execute(text("SET LOCAL enable_sort = off"))
        results = PostgresDocumentRepository(session).semantic_search(
            org_id, question, settings.embedding_model, limit=5
        )
        # Index scans made so far in THIS transaction, counted by PostgreSQL.
        scans = session.scalar(
            text("SELECT pg_stat_get_xact_numscans('ix_document_chunks_embedding'::regclass)")
        )
    return results, scans


def test_the_search_query_can_use_the_vector_index(question):
    """If the query sorted by a different operator than the index was built for
    (for example <-> against a cosine index), PostgreSQL would silently fall back
    to reading every row - correct results, v8 speed. The scan counter catches it."""
    results, scans = search_through_the_index(LARGE_ORG, question)

    assert scans >= 1
    assert len(results) == 5
    # Closest first: similarity only ever goes down the list.
    ranks = [hit.rank for hit in results]
    assert ranks == sorted(ranks, reverse=True)
    assert ranks[0] > 0.5  # the large organisation's chunks are close to the question


def test_a_small_organisation_still_gets_its_results_from_the_index(question):
    """Without an iterative scan, the index hands back its 40 nearest chunks -
    all from the large organisation - and the WHERE clause then removes every
    one of them: the small organisation gets NOTHING, although it has 3 chunks.
    Verified to fail with the `hnsw.iterative_scan` line removed."""
    results, scans = search_through_the_index(SMALL_ORG, question)

    assert scans >= 1
    assert len(results) == 3
    assert {hit.document_title for hit in results} == {"Org 1 handbook"}
