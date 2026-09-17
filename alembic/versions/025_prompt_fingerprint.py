"""What the app last wrote as an agent's prompt

Revision ID: 025
Revises: 024
Create Date: 2026-09-16 12:30:00.000000

``finance agents resync-prompts`` overwrites an agent's system prompt with
the one in code, and nothing else. It could not tell a prompt from an
older release - which it should replace - from one somebody rewrote in
the dashboard - which it should not. The fingerprint is the app's own
last write; a row that no longer matches it was edited by a person.
"""

import sqlalchemy as sa

from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent", sa.Column("prompt_fingerprint", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("prompt_fingerprint")
