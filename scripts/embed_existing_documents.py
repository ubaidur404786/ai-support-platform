"""Give embeddings to documents that were processed before v8.

The v8 migration adds an empty `embedding` column; it does not fill it, because
running the model inside a migration would make it slow and dependent on a
model download. Until this script runs, those documents are still found by
keyword search, but not by semantic search.

Also run it after changing EMBEDDING_MODEL: every document embedded by a
different model is embedded again with the new one.

From the repository root, after `alembic upgrade head`:
    python scripts/embed_existing_documents.py                      # everything
    python scripts/embed_existing_documents.py --organization-id 6  # one organisation

It prints how many chunks each organisation is waiting with, first. Embedding
runs at a few chunks per second on a laptop CPU (measured in v8), so 100,000
chunks is hours of work: start with the organisations you need.

Safe to stop and run again: each document is committed on its own, and
documents that already use the current model are skipped. It only takes READY
documents, so it never touches a document the worker is processing.
"""

import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import func, or_, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Registers the tables documents point at (see the same lines in app/worker.py).
import app.auth.models  # noqa: E402,F401
import app.tickets.models  # noqa: E402,F401
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.documents.models import READY, Document  # noqa: E402
from app.documents.processing import embed_existing_document  # noqa: E402

# READY documents whose chunks were not embedded by the current model.
NEEDS_EMBEDDING = (Document.status == READY) & or_(
    Document.embedding_model.is_(None),
    Document.embedding_model != settings.embedding_model,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, help="Only this organisation.")
    args = parser.parse_args()

    with SessionLocal() as session:
        waiting = session.execute(
            select(Document.organization_id, func.count(), func.sum(Document.chunk_count))
            .where(NEEDS_EMBEDDING)
            .group_by(Document.organization_id)
            .order_by(Document.organization_id)
        ).all()
        print(f"Waiting for {settings.embedding_model}:")
        for organization_id, documents, chunks in waiting:
            print(f"  organisation {organization_id}: {documents} documents, {chunks} chunks")

        query = select(Document.id).where(NEEDS_EMBEDDING).order_by(Document.id)
        if args.organization_id is not None:
            query = query.where(Document.organization_id == args.organization_id)
        ids = list(session.scalars(query))
        print(f"Embedding {len(ids)} documents")

        start = time.perf_counter()
        total_chunks = 0
        for number, document_id in enumerate(ids, start=1):
            document = session.get(Document, document_id)
            if document is None:  # deleted since the list was read
                continue
            chunks = embed_existing_document(session, document)
            total_chunks += chunks
            print(f"  [{number}/{len(ids)}] document {document_id}: {chunks} chunks")

    seconds = time.perf_counter() - start
    print(f"done: {total_chunks} chunks in {seconds:.1f} s")


if __name__ == "__main__":
    main()
