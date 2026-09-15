"""The page a figure was read off

Revision ID: 018
Revises: 017
Create Date: 2026-09-15 04:10:00.000000

"Read off the pension portal" is a note, not a way back. A year later
the question is which site, and the address is the answer - so the fact
carries it beside the sentence describing where it came from.

http and https only, checked on the way in: a stored address is
rendered as a link the reader clicks, and a ``javascript:`` href is a
script the page runs.
"""

import sqlalchemy as sa

from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fact", sa.Column("source_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fact") as batch:
        batch.drop_column("source_url")
