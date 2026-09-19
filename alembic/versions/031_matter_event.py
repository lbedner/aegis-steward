"""matter_event: the part of a matter's story that leaves no paper

The timeline (ST-10) is derived from dated rows that already exist - a
request received, a figure as of a date, an ask answered - so it can
never disagree with them. The call, the mailing and the office visit
are the only part nothing records, and they get the one table.

Revision ID: 031
Revises: 030
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "matter_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("matter_id", sa.Integer(), nullable=False),
        # The day it happened, not the day it was typed.
        sa.Column("occurred_at", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("party_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('call', 'mailed', 'visit', 'note')",
            name="ck_matter_event_kind",
        ),
    )
    op.create_index("ix_matter_event_matter", "matter_event", ["matter_id"])
    op.create_index("ix_matter_event_occurred", "matter_event", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_matter_event_occurred", table_name="matter_event")
    op.drop_index("ix_matter_event_matter", table_name="matter_event")
    op.drop_table("matter_event")
