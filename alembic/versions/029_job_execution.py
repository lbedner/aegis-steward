"""job_execution: the scheduler's run history, which had no migration

The table has a model and has always been created by
``SQLModel.metadata.create_all`` at startup, which is the very habit
#163 removes. Found by the check that replaces it: with create_all gone,
a fresh database came up missing this table and named it.

Every install that has been running already HAS the table, built by
create_all, so the startup adoption path stamps this rather than
replaying it. That is what the signature entry is for.

Revision ID: 029
Revises: 028
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_execution",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=191), nullable=False),
        sa.Column("job_name", sa.String(length=255), nullable=False),
        sa.Column("scheduled_run_time", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("traceback", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_execution_job_id", "job_execution", ["job_id"])
    op.create_index("ix_job_execution_started_at", "job_execution", ["started_at"])
    op.create_index("ix_job_execution_status", "job_execution", ["status"])


def downgrade() -> None:
    op.drop_index("ix_job_execution_status", table_name="job_execution")
    op.drop_index("ix_job_execution_started_at", table_name="job_execution")
    op.drop_index("ix_job_execution_job_id", table_name="job_execution")
    op.drop_table("job_execution")
