"""Add an embedding to every chunk and record which model made it

Revision ID: d7a2c4e9b1f5
Revises: 4b9d2e6f8a13
Create Date: 2026-09-28 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7a2c4e9b1f5'
down_revision: Union[str, Sequence[str], None] = '4b9d2e6f8a13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Both nullable: existing chunks have no embedding yet. They are filled in by
    # scripts/embed_existing_documents.py, not here - running a neural network
    # inside a migration would make the migration take minutes and depend on a
    # model download.
    op.add_column("document_chunks", sa.Column("embedding", sa.LargeBinary(), nullable=True))
    op.add_column(
        "documents", sa.Column("embedding_model", sa.String(length=200), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "embedding_model")
    op.drop_column("document_chunks", "embedding")
