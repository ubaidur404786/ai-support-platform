"""Store chunk embeddings in a pgvector column with an HNSW index

Revision ID: e5f1a8c3d9b2
Revises: d7a2c4e9b1f5
Create Date: 2026-09-28 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import numpy as np
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'e5f1a8c3d9b2'
down_revision: Union[str, Sequence[str], None] = 'd7a2c4e9b1f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Rows converted per round trip, so a large table is never held in memory at once.
BATCH_SIZE = 1_000


def _copy(source: str, target: str, convert) -> None:
    """Copy every non-NULL value from one column to another, converting it in Python.

    There is no SQL cast between v8's raw bytes and a pgvector value, so each
    vector makes one trip through Python. Rows are read in id order, a batch at a
    time: "the next 1,000 rows after the last id we saw".
    """
    connection = op.get_bind()
    last_id = 0
    while True:
        rows = connection.execute(
            sa.text(
                f"SELECT id, {source} FROM document_chunks "
                f"WHERE {source} IS NOT NULL AND id > :last_id ORDER BY id LIMIT :size"
            ),
            {"last_id": last_id, "size": BATCH_SIZE},
        ).all()
        if not rows:
            return
        connection.execute(
            sa.text(f"UPDATE document_chunks SET {target} = :value WHERE id = :id"),
            [{"id": row[0], "value": convert(row[1])} for row in rows],
        )
        last_id = rows[-1][0]


def _bytes_to_vector(data: bytes) -> str:
    # pgvector accepts a vector written as text: '[0.1,0.2,...]'.
    return "[" + ",".join(str(x) for x in np.frombuffer(data, dtype=np.float32)) + "]"


def _vector_to_bytes(value: str) -> bytes:
    return np.array(value.strip("[]").split(","), dtype=np.float32).tobytes()


def upgrade() -> None:
    """Upgrade schema."""
    # Installs pgvector's types, operators and index methods into this database.
    # The extension must exist in the PostgreSQL image (docker/postgres/Dockerfile).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # New column next to the old one, copy, drop the old one, take its name.
    op.add_column("document_chunks", sa.Column("embedding_vector", Vector(384), nullable=True))
    _copy("embedding", "embedding_vector", _bytes_to_vector)
    op.drop_column("document_chunks", "embedding")
    op.alter_column("document_chunks", "embedding_vector", new_column_name="embedding")

    # Built after the copy: filling an indexed column row by row is slower than
    # building the index once over the finished data.
    op.create_index(
        "ix_document_chunks_embedding",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_document_chunks_embedding", table_name="document_chunks")
    op.add_column("document_chunks", sa.Column("embedding_bytes", sa.LargeBinary(), nullable=True))
    # Selected as text so the conversion does not depend on pgvector's Python types.
    _copy("embedding::text", "embedding_bytes", _vector_to_bytes)
    op.drop_column("document_chunks", "embedding")
    op.alter_column("document_chunks", "embedding_bytes", new_column_name="embedding")
    op.execute("DROP EXTENSION IF EXISTS vector")
