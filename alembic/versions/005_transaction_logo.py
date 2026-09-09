"""A provider-neutral logo URL on a transaction

Revision ID: 005
Revises: 004
Create Date: 2026-09-09 02:10:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the logo a source attached to a transaction."""
    op.add_column(
        "finance_transaction", sa.Column("logo_url", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("finance_transaction", "logo_url")
