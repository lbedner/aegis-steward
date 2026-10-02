"""agent_prompt_change: every change to an agent's system prompt

The agent row is the only source of its prompt (#355); code seeds it once.
``finance agents resync-prompts`` and the fingerprint that guarded it are
gone, and this table records what each prompt was and why it changed.

Each agent's current prompt goes in as its first change, so history
starts at what the install has rather than at the next edit.

Revision ID: 051
Revises: 050
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "051"
down_revision: str | None = "050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_prompt_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_prompt_change_agent_id", "agent_prompt_change", ["agent_id"]
    )
    op.execute(
        "INSERT INTO agent_prompt_change "
        "(agent_id, system_prompt, source, note, created_at) "
        "SELECT id, system_prompt, 'migration', "
        "'the prompt on the row when history began', CURRENT_TIMESTAMP "
        "FROM agent"
    )
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("prompt_fingerprint")


def downgrade() -> None:
    op.add_column(
        "agent", sa.Column("prompt_fingerprint", sa.String(length=64), nullable=True)
    )
    op.drop_index("ix_agent_prompt_change_agent_id", table_name="agent_prompt_change")
    op.drop_table("agent_prompt_change")
