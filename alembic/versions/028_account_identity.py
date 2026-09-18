"""Which account this is, and who holds it

Revision ID: 028
Revises: 027
Create Date: 2026-09-17 18:10:00.000000

Chase checking's page was empty because neither half was modelled. The
bank's routing number goes on the INSTITUTION: every account there
shares it, and a copy per account is two copies that can disagree. The
account number goes on the ACCOUNT and is encrypted, because with the
routing number beside it, it is enough for somebody to pull an ACH
debit. ``mask`` is derived from its last four on write, so the masked
display never has to decrypt anything.
"""

import sqlalchemy as sa

from alembic import op

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None

SCHEMA = None


def upgrade() -> None:
    op.add_column(
        "finance_institution",
        sa.Column("routing_number", sa.String(length=9), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "finance_account",
        sa.Column("account_number_encrypted", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    with op.batch_alter_table("finance_account", schema=SCHEMA) as batch:
        batch.drop_column("account_number_encrypted")
    with op.batch_alter_table("finance_institution", schema=SCHEMA) as batch:
        batch.drop_column("routing_number")
