"""How a debt can be paid down, not just what it costs

Revision ID: 009
Revises: 008
Create Date: 2026-09-13 22:20:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Two answers a payoff plan needs and the ledger could not hold.

    A rate says what a debt COSTS; neither of these does. Whether paying
    early is penalised, and whether money above the required payment
    reduces principal or merely pays next month early, decide whether an
    accelerated payoff works at all - and both are claims about somebody's
    contract, so they are recorded when the borrower confirms them and
    are ``unknown`` until then rather than assumed.
    """
    op.add_column(
        "finance_liability_detail",
        sa.Column("prepayment_penalty", sa.String(16), nullable=True),
    )
    op.add_column(
        "finance_liability_detail",
        sa.Column("extra_payment_treatment", sa.String(24), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("finance_liability_detail", "extra_payment_treatment")
    op.drop_column("finance_liability_detail", "prepayment_penalty")
