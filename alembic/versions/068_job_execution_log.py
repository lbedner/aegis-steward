"""scheduler: what each run logged, kept with its history row

Revision ID: 068
Revises: 067
Create Date: 2026-10-22 18:30:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("job_execution", schema=None) as batch_op:
        batch_op.add_column(sa.Column("log", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("job_execution", schema=None) as batch_op:
        batch_op.drop_column("log")
