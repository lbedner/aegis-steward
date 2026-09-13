"""Every ledger row can name the run that brought it

Revision ID: 012
Revises: 011
Create Date: 2026-09-14 15:10:00.000000

Transactions and trades have carried ``import_batch_id`` since imports
were written; holdings, documents and valuations never did. So three of
the five ledgers can say WHEN a row arrived (``created_at``) but not
WHICH run brought it - and "what is this row and where did it come
from" has to be answerable the same way everywhere or it is not a
system, it is a feature two tables happen to have.

Nullable everywhere, because a row can be typed by hand: a position
entered in the Positions dialog arrived from nobody, and that is a real
answer rather than a gap.
"""

import sqlalchemy as sa

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None

_TABLES = ("finance_holding", "finance_valuation", "document")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("import_batch_id", sa.Integer(), nullable=True))
        op.create_index(
            f"ix_{table}_batch", table, ["import_batch_id"], unique=False
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_batch", table_name=table)
        op.drop_column(table, "import_batch_id")
