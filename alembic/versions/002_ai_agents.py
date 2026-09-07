"""AI agent registry tables (agents, tools, agent-tool links)

Revision ID: 002
Revises: 001
Create Date: 2026-09-07 19:50:32.597584

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ai_agents service tables."""

    # Create agent table
    op.create_table(
        "agent",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("system_prompt", sa.String(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False, default=0.7),
        sa.Column("max_tokens", sa.Integer(), nullable=False, default=1000),
        sa.Column("memory_modules", sa.JSON(), nullable=False, default=[]),
        sa.Column("knowledge_base_ids", sa.JSON(), nullable=False, default=[]),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("code_mode", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_agent_slug"), "agent", ["slug"], unique=True)

    op.create_index(op.f("ix_agent_model_id"), "agent", ["model_id"])

    # Create tool table
    op.create_table(
        "tool",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_tool_name"), "tool", ["name"], unique=True)

    # Create agent_tool table
    op.create_table(
        "agent_tool",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("tool_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tool_id"], ["tool.id"], ondelete="CASCADE"),
    )

    op.create_index(
        op.f("uq_agent_tool_pair"), "agent_tool", ["agent_id", "tool_id"], unique=True
    )

    op.create_index(op.f("ix_agent_tool_tool_id"), "agent_tool", ["tool_id"])

    # Create memory_module table
    op.create_table(
        "memory_module",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("prompt_content", sa.String(), nullable=True),
        sa.Column("fetch_function", sa.String(), nullable=True),
        sa.Column("context_key", sa.String(), nullable=False),
        sa.Column("supports_days_back", sa.Boolean(), nullable=False, default=False),
        sa.Column("default_days_back", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, default=100),
        sa.Column("token_estimate", sa.Integer(), nullable=False, default=0),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_memory_module_slug"), "memory_module", ["slug"], unique=True
    )

    # Create agent_user_memory table
    op.create_table(
        "agent_user_memory",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("memory", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_agent_user_memory_user_id"),
        "agent_user_memory",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    """Reverse ai_agents migration."""

    op.drop_index(op.f("ix_agent_user_memory_user_id"), table_name="agent_user_memory")

    op.drop_table("agent_user_memory")

    op.drop_index(op.f("ix_memory_module_slug"), table_name="memory_module")

    op.drop_table("memory_module")

    op.drop_index(op.f("uq_agent_tool_pair"), table_name="agent_tool")

    op.drop_index(op.f("ix_agent_tool_tool_id"), table_name="agent_tool")

    op.drop_table("agent_tool")

    op.drop_index(op.f("ix_tool_name"), table_name="tool")

    op.drop_table("tool")

    op.drop_index(op.f("ix_agent_slug"), table_name="agent")

    op.drop_index(op.f("ix_agent_model_id"), table_name="agent")

    op.drop_table("agent")
