"""Storage for documents and their chunks, backed by PostgreSQL."""

# Annotations are read lazily. Without this, "list[...]" inside the class body
# would refer to the method named list defined above it, not the built-in type.
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AlreadyExistsError, StorageError, translated_errors
from app.documents.models import PROCESSING, QUEUED, Document, DocumentChunk, DocumentFile

Match = Literal["any", "all"]

# See semantic_search. A constant rather than a setting: changing it is a
# decision to re-measure recall, not a deployment detail.
HNSW_EF_SEARCH = 100


@dataclass(frozen=True)
class SearchHit:
    document_id: int
    document_title: str
    chunk_index: int
    text: str
    rank: float


class DocumentRepository(Protocol):
    # The same rule as tickets (ADR-013): organization_id is required and first
    # on every read, so forgetting it is a TypeError rather than a leak.
    def add(self, document: Document, data: bytes) -> Document: ...

    def get(self, organization_id: int, document_id: int) -> Document | None: ...

    def get_by_hash(self, organization_id: int, sha256: str) -> Document | None: ...

    def list(self, organization_id: int, limit: int, offset: int) -> list[Document]: ...

    def count(self, organization_id: int) -> int: ...

    def count_pending(self, organization_id: int) -> int: ...

    def delete(self, organization_id: int, document_id: int) -> bool: ...

    def search(
        self, organization_id: int, words: list[str], limit: int, match: Match = "any"
    ) -> list[SearchHit]: ...

    def semantic_search(
        self, organization_id: int, query_vector: np.ndarray, model_name: str, limit: int
    ) -> list[SearchHit]: ...


# Only these characters reach to_tsquery. Its syntax gives meaning to & | ! ( ) :
# and quotes; a user typing one of them would otherwise cause a syntax error - or
# change the query. The words are rebuilt from letters and digits alone.
_SAFE_WORD = re.compile(r"^[a-z0-9]+$")


class PostgresDocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, document: Document, data: bytes) -> Document:
        """Store a new document and its uploaded file in ONE transaction.

        Either both rows are written or neither is. A queued document without
        its file could never be processed; a file without its document would
        never be found.
        """
        try:
            self._session.add(document)
            # flush sends the INSERT without committing, so PostgreSQL assigns
            # document.id - which the file row needs - while the transaction is
            # still open and can still be rolled back.
            self._session.flush()
            self._session.add(DocumentFile(document_id=document.id, data=data))
            self._session.commit()
        except IntegrityError as error:
            # Caught before SQLAlchemyError, its parent class. But IntegrityError
            # also covers NOT NULL and foreign-key violations, which are bugs, not
            # duplicates - reporting those as 409 would hide them. SQLSTATE 23505
            # is specifically "unique violation": the (organization_id, sha256)
            # constraint fired because a concurrent upload of the same file won.
            self._session.rollback()
            if getattr(error.orig, "sqlstate", None) == "23505":
                raise AlreadyExistsError(
                    "This document has already been uploaded"
                ) from error
            raise StorageError(str(error)) from error
        except SQLAlchemyError as error:
            self._session.rollback()
            raise StorageError(str(error)) from error
        return document

    def get(self, organization_id: int, document_id: int) -> Document | None:
        with translated_errors(self._session):
            return self._session.scalar(
                select(Document).where(
                    Document.id == document_id,
                    Document.organization_id == organization_id,
                )
            )

    def get_by_hash(self, organization_id: int, sha256: str) -> Document | None:
        with translated_errors(self._session):
            return self._session.scalar(
                select(Document).where(
                    Document.organization_id == organization_id,
                    Document.sha256 == sha256,
                )
            )

    def list(self, organization_id: int, limit: int, offset: int) -> list[Document]:
        with translated_errors(self._session):
            # Newest first: the document someone just uploaded is the one they
            # are looking for. (Tickets still list oldest-first - recorded debt.)
            statement = (
                select(Document)
                .where(Document.organization_id == organization_id)
                .order_by(Document.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(self._session.scalars(statement))

    def count(self, organization_id: int) -> int:
        with translated_errors(self._session):
            statement = (
                select(func.count())
                .select_from(Document)
                .where(Document.organization_id == organization_id)
            )
            return self._session.scalar(statement) or 0

    def count_pending(self, organization_id: int) -> int:
        """Documents of this organisation still waiting for, or in, the worker."""
        with translated_errors(self._session):
            statement = (
                select(func.count())
                .select_from(Document)
                .where(
                    Document.organization_id == organization_id,
                    Document.status.in_([QUEUED, PROCESSING]),
                )
            )
            return self._session.scalar(statement) or 0

    def delete(self, organization_id: int, document_id: int) -> bool:
        with translated_errors(self._session):
            # One DELETE with both conditions: another organisation's document is
            # simply not matched, which the router reports as 404 (ADR-015). The
            # chunks and any waiting file go with it via ON DELETE CASCADE.
            result = self._session.execute(
                delete(Document).where(
                    Document.id == document_id,
                    Document.organization_id == organization_id,
                )
            )
            self._session.commit()
            return result.rowcount > 0

    def search(
        self, organization_id: int, words: list[str], limit: int, match: Match = "any"
    ) -> list[SearchHit]:
        if not words or not all(_SAFE_WORD.match(word) for word in words):
            # The service cleans the words; this is a second line of defence for
            # any other caller, because the words become query syntax below.
            raise ValueError("search words must be non-empty lowercase letters/digits")

        # "any" joins words with | (OR): a chunk matching some of the words is a
        # candidate, and ranking puts chunks matching more of them first. "all"
        # joins with & (AND). Natural-language questions rarely share every word
        # with the passage that answers them, so AND finds too little - the
        # evaluation measures exactly how much.
        operator = " | " if match == "any" else " & "
        query = func.to_tsquery("english", operator.join(words))

        # ts_rank scores a chunk by how often the query words occur in it. It is
        # a ranking of keyword overlap and knows nothing about meaning.
        #
        # ts_rank_cd ("cover density", which also rewards words appearing close
        # together) was the first choice, and measurement removed it: over 39,580
        # matching chunks it took 6,368 ms against 64 ms for ts_rank - ~100x - and
        # the evaluation did not get worse (hit@3 0.73 with both; MRR 0.64 -> 0.66).
        # Ranking runs once per MATCHING chunk, before LIMIT, so its per-row cost
        # multiplies with the number of matches.
        rank = func.ts_rank(DocumentChunk.search_vector, query)

        statement = (
            select(
                DocumentChunk.document_id,
                Document.title,
                DocumentChunk.chunk_index,
                DocumentChunk.text,
                rank.label("rank"),
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            # The tenant filter, on the chunk itself - not optional, not via the
            # join alone.
            .where(DocumentChunk.organization_id == organization_id)
            # @@ is "matches": uses the GIN index to find candidate chunks.
            .where(DocumentChunk.search_vector.op("@@")(query))
            # Chunk id as tie-breaker, so equal ranks come back in a stable order.
            .order_by(rank.desc(), DocumentChunk.id)
            .limit(limit)
        )
        with translated_errors(self._session):
            return [
                SearchHit(
                    document_id=row.document_id,
                    document_title=row.title,
                    chunk_index=row.chunk_index,
                    text=row.text,
                    rank=float(row.rank),
                )
                for row in self._session.execute(statement)
            ]

    def semantic_search(
        self, organization_id: int, query_vector: np.ndarray, model_name: str, limit: int
    ) -> list[SearchHit]:
        """The chunks whose meaning is closest to the question's.

        v8 read EVERY vector of the organisation into Python and compared them
        there: exact, but ~10 s at 50,000 chunks, almost all of it spent copying
        vectors out of the database (ADR-024). Since v9 PostgreSQL does the
        comparing itself (pgvector), and for a large organisation it walks the
        HNSW index instead of reading every row (ADR-025). Only the `limit`
        winning rows ever leave the database.
        """
        # <=> is pgvector's cosine DISTANCE: 0 = same direction, 2 = opposite.
        # Similarity is 1 - distance, the same score v8 returned.
        distance = DocumentChunk.embedding.cosine_distance(query_vector)
        with translated_errors(self._session):
            # By default an HNSW scan collects 40 candidates (hnsw.ef_search) and
            # only THEN applies the WHERE clause. If most of them belong to other
            # organisations, the search returns fewer than `limit` results - or
            # none. An iterative scan keeps walking the graph until enough rows
            # pass the filter. strict_order keeps the results sorted by distance.
            # SET LOCAL: only for this transaction, never leaks to other queries.
            self._session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            # How many candidates the index keeps while it walks the graph. More =
            # closer to the exact answer, a little slower. Measured in v9: 40 (the
            # default) found 98% of the true top 5 on real text, 100 found 99%,
            # for the same ~60 ms.
            self._session.execute(text(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}"))
            rows = self._session.execute(
                select(
                    DocumentChunk.document_id,
                    Document.title,
                    DocumentChunk.chunk_index,
                    DocumentChunk.text,
                    distance.label("distance"),
                )
                .join(Document, Document.id == DocumentChunk.document_id)
                # The tenant filter, on the chunk itself - as in keyword search.
                .where(DocumentChunk.organization_id == organization_id)
                # Only vectors made by the model that embedded the question.
                .where(Document.embedding_model == model_name)
                # Sorting by the distance alone is what lets PostgreSQL use the
                # index; adding a second sort key would switch it off.
                .order_by(distance)
                .limit(limit)
            ).all()
        return [
            SearchHit(
                document_id=row.document_id,
                document_title=row.title,
                chunk_index=row.chunk_index,
                text=row.text,
                rank=1 - float(row.distance),
            )
            for row in rows
        ]
