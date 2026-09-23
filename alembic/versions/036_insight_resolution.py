"""finance_insight.resolution: what an anomaly turned out to be

An insight was new, seen, dismissed or actioned - enough for a badge,
not enough to record that the large charge was the dentist and
expected, or a duplicate of Tuesday's. The assistant asked to be able
to say which, in the person's words, confirmed by the person (FW-09).

resolution is one of a small set, resolution_note the words, resolved_at
when. Kept with the row: the dedup key already stops the same
occurrence being raised again, so a resolved row is the record that the
question was settled.

Revision ID: 036
Revises: 035
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Batch mode: SQLite cannot add a CHECK constraint to a table in place.
    with op.batch_alter_table("finance_insight") as batch_op:
        batch_op.add_column(sa.Column("resolution", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("resolution_note", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("resolved_at", sa.DateTime(), nullable=True))
        batch_op.create_check_constraint(
            "ck_finance_insight_resolution",
            "resolution IN ('legitimate', 'duplicate', 'wrong_amount', "
            "'miscategorized', 'expected_missing', 'under_review', 'resolved')",
        )


def downgrade() -> None:
    with op.batch_alter_table("finance_insight") as batch_op:
        batch_op.drop_constraint("ck_finance_insight_resolution", type_="check")
        batch_op.drop_column("resolved_at")
        batch_op.drop_column("resolution_note")
        batch_op.drop_column("resolution")
