"""An ingestion run records what it brought

Revision ID: 011
Revises: 010
Create Date: 2026-09-14 14:30:00.000000

``finance_import_batch`` has said since it was written that it holds
"one row per ingestion run (a Plaid/SnapTrade sync pass, an uploaded
QIF/QFX/OFX/CSV file, or a manual bulk)", and its row counters suit a
file: total, inserted, updated, duplicate, error. A brokerage sync
brings things a file does not - holdings and trades - and a bank sync
can remove a transaction the provider retracted.

Rather than a column per provider tally, one JSON ``detail``. What a run
brought DIFFERS by where it came from, and the alternative is a table
that grows a column every time a new source counts something new.
"""

import sqlalchemy as sa

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_import_batch",
        sa.Column("detail", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("finance_import_batch", "detail")
