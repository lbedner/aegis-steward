"""An institution is owned curation, not just a provider directory

Revision ID: 007
Revises: 006
Create Date: 2026-09-11 18:10:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Who an account is with, and how to reach them.

    The table existed as a provider-only directory and on a real ledger
    it was empty, because nothing but the Plaid path ever wrote one.
    An owner id makes it curation like categories and merchants: NULL is
    a provider seed, an owner id is a bank somebody named themselves.
    """
    op.add_column(
        "finance_institution", sa.Column("owner_user_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "finance_institution",
        sa.Column("normalized_name", sa.String(128), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_finance_institution_owner", "finance_institution", ["owner_user_id"]
    )
    op.create_index(
        "ix_finance_institution_normalized", "finance_institution", ["normalized_name"]
    )
    # One name is one row PER OWNER; the provider seeds keep their own
    # uniqueness on (provider, provider_institution_id).
    op.create_index(
        "uq_finance_institution_owner_name",
        "finance_institution",
        ["owner_user_id", "normalized_name"],
        unique=True,
        sqlite_where=sa.text("owner_user_id IS NOT NULL"),
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_finance_institution_owner_name", "finance_institution")
    op.drop_index("ix_finance_institution_normalized", "finance_institution")
    op.drop_index("ix_finance_institution_owner", "finance_institution")
    op.drop_column("finance_institution", "normalized_name")
    op.drop_column("finance_institution", "owner_user_id")
