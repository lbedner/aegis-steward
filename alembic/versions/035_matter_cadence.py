"""matter.cadence, matter.next_expected_on: when the next request is due

A Medicaid renewal comes every year, so the one thing certain about
next August is that another letter asks for the same proof - and the
last one gave eight days. A deadline can only nag about a request that
already exists; this is the date the next one is expected to ARRIVE, and
the cadence that moves it on when it does (ST-11).

The date is the reminder. Snoozing is moving it; there is no separate
reminder state to go stale.

Revision ID: 035
Revises: 034
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Batch mode: SQLite cannot add a CHECK constraint to a table in place.
    with op.batch_alter_table("matter") as batch_op:
        batch_op.add_column(sa.Column("cadence", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("next_expected_on", sa.Date(), nullable=True))
        batch_op.create_check_constraint(
            "ck_matter_cadence", "cadence IN ('annual', 'semiannual', 'quarterly')"
        )


def downgrade() -> None:
    with op.batch_alter_table("matter") as batch_op:
        batch_op.drop_constraint("ck_matter_cadence", type_="check")
        batch_op.drop_column("next_expected_on")
        batch_op.drop_column("cadence")
