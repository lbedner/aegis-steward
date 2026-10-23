"""database: saved queries, the statements Overseer's Query keeps

Revision ID: 067
Revises: 066
Create Date: 2026-10-22 18:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_query",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("saved_by", sa.String(320), nullable=True),
        sa.Column("saved_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("saved_query")
