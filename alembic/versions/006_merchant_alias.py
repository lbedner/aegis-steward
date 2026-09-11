"""Bank descriptor -> payee, so naming a payee survives the next import

Revision ID: 006
Revises: 005
Create Date: 2026-09-10 22:30:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """The merchant-side counterpart to finance_category_alias."""
    op.create_table(
        "finance_merchant_alias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("merchant_id", sa.Integer(), nullable=False),
        sa.Column("alias_text", sa.String(), nullable=False),
        sa.Column("normalized_alias", sa.String(), nullable=False),
        sa.Column("is_ambiguous", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["finance_merchant.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_finance_merchalias_owner", "finance_merchant_alias", ["owner_user_id"]
    )
    op.create_index(
        "ix_finance_merchalias_merchant", "finance_merchant_alias", ["merchant_id"]
    )
    op.create_index(
        "ix_finance_merchalias_normalized",
        "finance_merchant_alias",
        ["normalized_alias"],
    )
    # A payee key holds one answer: being taught a second payee rewrites
    # the row and flags it, rather than landing beside what it replaces.
    op.create_index(
        "uq_finance_merchalias_owner_norm",
        "finance_merchant_alias",
        ["owner_user_id", "normalized_alias"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_finance_merchalias_owner_norm", "finance_merchant_alias")
    op.drop_index("ix_finance_merchalias_normalized", "finance_merchant_alias")
    op.drop_index("ix_finance_merchalias_merchant", "finance_merchant_alias")
    op.drop_index("ix_finance_merchalias_owner", "finance_merchant_alias")
    op.drop_table("finance_merchant_alias")
