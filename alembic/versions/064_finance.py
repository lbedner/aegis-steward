"""finance: the day an account closed (#315)

Revision ID: 064
Revises: 063
Create Date: 2026-10-19 12:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None
aegis_stamp_signature = ("column", "finance_account", "closed_on")


def upgrade() -> None:
    with op.batch_alter_table("finance_account", schema=None) as batch_op:
        batch_op.add_column(sa.Column("closed_on", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("finance_account", schema=None) as batch_op:
        batch_op.drop_column("closed_on")
