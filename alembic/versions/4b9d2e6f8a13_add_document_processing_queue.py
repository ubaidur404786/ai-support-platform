"""Add processing status to documents and a table for files waiting to be processed

Revision ID: 4b9d2e6f8a13
Revises: 8c3e5d7a1f20
Create Date: 2026-09-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b9d2e6f8a13'
down_revision: Union[str, Sequence[str], None] = '8c3e5d7a1f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Every document that exists before v7 was processed inside its upload
    # request, so it is already finished. server_default fills the existing rows
    # with 'ready' / 0; it is removed straight after, so new rows take their
    # values from the application ('queued'), not from a leftover default.
    op.add_column(
        "documents",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ready"),
    )
    op.alter_column("documents", "status", server_default=None)
    op.add_column(
        "documents",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("documents", "attempts", server_default=None)
    op.add_column("documents", sa.Column("error_message", sa.String(length=500), nullable=True))
    op.add_column(
        "documents",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_documents_status", "documents", ["status"])

    # The text does not exist yet while a document waits in the queue.
    op.alter_column("documents", "content", existing_type=sa.Text(), nullable=True)

    op.create_table(
        "document_files",
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("document_id"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("document_files")
    # v6 cannot represent an unfinished document: it has no status column and
    # requires content. Documents that are not 'ready' are deleted - including
    # their waiting files, which is why document_files is dropped first.
    op.execute("DELETE FROM documents WHERE status <> 'ready'")
    op.alter_column("documents", "content", existing_type=sa.Text(), nullable=False)
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_column("documents", "processed_at")
    op.drop_column("documents", "processing_started_at")
    op.drop_column("documents", "error_message")
    op.drop_column("documents", "attempts")
    op.drop_column("documents", "status")
