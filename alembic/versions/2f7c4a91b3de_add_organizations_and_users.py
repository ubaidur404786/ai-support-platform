"""Add organizations and users, attach tickets to an organization

Revision ID: 2f7c4a91b3de
Revises: 131691f93bab
Create Date: 2026-09-24 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2f7c4a91b3de'
down_revision: Union[str, Sequence[str], None] = '131691f93bab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    # Attaching organization_id to tickets takes three steps, because the table
    # already holds rows and NOT NULL cannot be applied to a column that is null.
    #
    # Step 1: add the column, temporarily nullable.
    op.add_column("tickets", sa.Column("organization_id", sa.Integer(), nullable=True))

    # Step 2: give every existing row an owner. This organisation is also the one
    # the first user registers into, so it is not throwaway data.
    op.execute(
        "INSERT INTO organizations (name, created_at) VALUES ('default', now())"
    )
    op.execute(
        "UPDATE tickets SET organization_id = "
        "(SELECT id FROM organizations WHERE name = 'default')"
    )

    # Step 3: no row is null any more, so the constraint can be enforced. Adding
    # the foreign key last means PostgreSQL validates it against data that is
    # already correct.
    op.alter_column("tickets", "organization_id", nullable=False)
    op.create_foreign_key(
        "fk_tickets_organization_id",
        "tickets",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_index("ix_tickets_organization_id", "tickets", ["organization_id"])


def downgrade() -> None:
    """Downgrade schema."""
    # Reverse order: nothing may be dropped while something still references it.
    op.drop_index("ix_tickets_organization_id", table_name="tickets")
    op.drop_constraint("fk_tickets_organization_id", "tickets", type_="foreignkey")
    op.drop_column("tickets", "organization_id")

    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
