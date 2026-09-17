"""The charge that paid a claim

Revision ID: 027
Revises: 026
Create Date: 2026-09-17 02:30:00.000000

An EOB said Marisa owed $146 to the provider, and the ledger showed a
$146 charge to Endodontics on the day of the visit, and nothing tied
them. With the link, "owed to providers" is a number that goes down
when you pay.
"""

import sqlalchemy as sa

from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "insurance_claim",
        sa.Column("paid_transaction_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_insurance_claim_paid", "insurance_claim", ["paid_transaction_id"])


def downgrade() -> None:
    with op.batch_alter_table("insurance_claim") as batch:
        batch.drop_index("ix_insurance_claim_paid")
        batch.drop_column("paid_transaction_id")
