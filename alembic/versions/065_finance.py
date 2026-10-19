"""finance: a loan's escrow (#318), a budget month as it ended (#319)

Revision ID: 065
Revises: 064
Create Date: 2026-10-19 16:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None
aegis_stamp_signature = ("table", "finance_budget_month")


def upgrade() -> None:
    with op.batch_alter_table("finance_liability_detail", schema=None) as batch_op:
        batch_op.add_column(sa.Column("escrow_amount", sa.BigInteger(), nullable=True))
    op.create_table(
        "finance_budget_month",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("actuals", sa.JSON(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    with op.batch_alter_table("finance_budget_month", schema=None) as batch_op:
        batch_op.create_index(
            "uq_finance_budget_month", ["owner_user_id", "period_month"], unique=True
        )


def downgrade() -> None:
    op.drop_table("finance_budget_month")
    with op.batch_alter_table("finance_liability_detail", schema=None) as batch_op:
        batch_op.drop_column("escrow_amount")
