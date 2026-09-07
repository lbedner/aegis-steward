"""AI service tables (LLM catalog, usage tracking, conversations)

Revision ID: 001
Revises: None
Create Date: 2026-09-07 06:14:39.832328

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ai service tables."""

    # Create llm_org table
    op.create_table(
        "llm_org",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("homepage", sa.String(), nullable=True),
        sa.Column("icon_b64", sa.String(), nullable=True),
        sa.Column("color", sa.String(), nullable=False, default="#6B7280"),
        sa.Column("icon_path", sa.String(), nullable=False, default=""),
        sa.Column("api_base", sa.String(), nullable=True),
        sa.Column("auth_method", sa.String(), nullable=False, default="api-key"),
        sa.Column("source", sa.String(), nullable=False, default="catalog"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_llm_org_slug"), "llm_org", ["slug"], unique=True)

    op.create_index(op.f("ix_llm_org_name"), "llm_org", ["name"])

    # Create llm_org_role table
    op.create_table(
        "llm_org_role",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["llm_org.id"], ondelete="CASCADE"),
        sa.CheckConstraint("role IN ('maker', 'server')", name="ck_llm_org_role_role"),
    )

    op.create_index(op.f("ix_llm_org_role_org_id"), "llm_org_role", ["org_id"])

    # Create large_language_model table
    op.create_table(
        "large_language_model",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, default=""),
        sa.Column("context_window", sa.Integer(), nullable=False, default=4096),
        sa.Column("training_data", sa.String(), nullable=False, default=""),
        sa.Column("streamable", sa.Boolean(), nullable=False, default=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, default=True),
        sa.Column("color", sa.String(), nullable=False, default="#6B7280"),
        sa.Column("icon_path", sa.String(), nullable=False, default=""),
        sa.Column("license", sa.String(), nullable=True),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("released_on", sa.DateTime(), nullable=True),
        sa.Column("family", sa.String(), nullable=True),
        sa.Column("served_by_org_id", sa.Integer(), nullable=True),
        sa.Column("made_by_org_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["served_by_org_id"], ["llm_org.id"]),
        sa.ForeignKeyConstraint(["made_by_org_id"], ["llm_org.id"]),
    )

    op.create_index(
        op.f("ix_large_language_model_model_id"),
        "large_language_model",
        ["model_id"],
        unique=True,
    )

    op.create_index(
        op.f("ix_large_language_model_served_by_org_id"),
        "large_language_model",
        ["served_by_org_id"],
    )

    op.create_index(
        op.f("ix_large_language_model_made_by_org_id"),
        "large_language_model",
        ["made_by_org_id"],
    )

    # Create llm_active_selection table
    op.create_table(
        "llm_active_selection",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_llm_active_selection_owner_user_id"),
        "llm_active_selection",
        ["owner_user_id"],
    )

    op.create_index(
        op.f("ix_llm_active_selection_model_id"), "llm_active_selection", ["model_id"]
    )

    # Create llm_deployment table
    op.create_table(
        "llm_deployment",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("llm_id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("speed", sa.Integer(), nullable=False, default=50),
        sa.Column("intelligence", sa.Integer(), nullable=False, default=50),
        sa.Column("reasoning", sa.Integer(), nullable=False, default=50),
        sa.Column("output_max_tokens", sa.Integer(), nullable=False, default=4096),
        sa.Column("function_calling", sa.Boolean(), nullable=False, default=False),
        sa.Column("input_cache", sa.Boolean(), nullable=False, default=False),
        sa.Column("structured_output", sa.Boolean(), nullable=False, default=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["llm_id"], ["large_language_model.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["llm_org.id"]),
    )

    op.create_index(op.f("ix_llm_deployment_llm_id"), "llm_deployment", ["llm_id"])

    op.create_index(op.f("ix_llm_deployment_org_id"), "llm_deployment", ["org_id"])

    op.create_index(
        op.f("ix_llm_deployment_org_unique"),
        "llm_deployment",
        ["llm_id", "org_id"],
        unique=True,
    )

    # Create llm_modality table
    op.create_table(
        "llm_modality",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("llm_id", sa.Integer(), nullable=False),
        sa.Column("modality", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["llm_id"], ["large_language_model.id"]),
    )

    op.create_index(op.f("ix_llm_modality_llm_id"), "llm_modality", ["llm_id"])

    op.create_index(
        op.f("ix_llm_modality_unique"),
        "llm_modality",
        ["llm_id", "modality", "direction"],
        unique=True,
    )

    # Create llm_price table
    op.create_table(
        "llm_price",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("llm_id", sa.Integer(), nullable=False),
        sa.Column("input_cost_per_token", sa.Float(), nullable=False),
        sa.Column("output_cost_per_token", sa.Float(), nullable=False),
        sa.Column("cache_input_cost_per_token", sa.Float(), nullable=True),
        sa.Column("effective_date", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["llm_org.id"]),
        sa.ForeignKeyConstraint(["llm_id"], ["large_language_model.id"]),
    )

    op.create_index(op.f("ix_llm_price_org_id"), "llm_price", ["org_id"])

    op.create_index(op.f("ix_llm_price_llm_id"), "llm_price", ["llm_id"])

    op.create_index(
        op.f("ix_llm_price_effective_date"), "llm_price", ["effective_date"]
    )

    # Create llm_usage table
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_cost", sa.Float(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False, default=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_llm_usage_model_id"), "llm_usage", ["model_id"])

    op.create_index(op.f("ix_llm_usage_user_id"), "llm_usage", ["user_id"])

    op.create_index(op.f("ix_llm_usage_timestamp"), "llm_usage", ["timestamp"])

    op.create_index(op.f("ix_llm_usage_action"), "llm_usage", ["action"])

    # Create conversation table
    op.create_table(
        "conversation",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=False, default={}),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_conversation_user_id"), "conversation", ["user_id"])

    # Create conversation_message table
    op.create_table(
        "conversation_message",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=False, default={}),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"]),
    )

    op.create_index(
        op.f("ix_conversation_message_conversation_id"),
        "conversation_message",
        ["conversation_id"],
    )

    op.create_index(
        op.f("ix_conversation_message_timestamp"), "conversation_message", ["timestamp"]
    )


def downgrade() -> None:
    """Reverse ai migration."""

    op.drop_index(
        op.f("ix_conversation_message_conversation_id"),
        table_name="conversation_message",
    )

    op.drop_index(
        op.f("ix_conversation_message_timestamp"), table_name="conversation_message"
    )

    op.drop_table("conversation_message")

    op.drop_index(op.f("ix_conversation_user_id"), table_name="conversation")

    op.drop_table("conversation")

    op.drop_index(op.f("ix_llm_usage_model_id"), table_name="llm_usage")

    op.drop_index(op.f("ix_llm_usage_user_id"), table_name="llm_usage")

    op.drop_index(op.f("ix_llm_usage_timestamp"), table_name="llm_usage")

    op.drop_index(op.f("ix_llm_usage_action"), table_name="llm_usage")

    op.drop_table("llm_usage")

    op.drop_index(op.f("ix_llm_price_org_id"), table_name="llm_price")

    op.drop_index(op.f("ix_llm_price_llm_id"), table_name="llm_price")

    op.drop_index(op.f("ix_llm_price_effective_date"), table_name="llm_price")

    op.drop_table("llm_price")

    op.drop_index(op.f("ix_llm_modality_llm_id"), table_name="llm_modality")

    op.drop_index(op.f("ix_llm_modality_unique"), table_name="llm_modality")

    op.drop_table("llm_modality")

    op.drop_index(op.f("ix_llm_deployment_llm_id"), table_name="llm_deployment")

    op.drop_index(op.f("ix_llm_deployment_org_id"), table_name="llm_deployment")

    op.drop_index(op.f("ix_llm_deployment_org_unique"), table_name="llm_deployment")

    op.drop_table("llm_deployment")

    op.drop_index(
        op.f("ix_llm_active_selection_owner_user_id"), table_name="llm_active_selection"
    )

    op.drop_index(
        op.f("ix_llm_active_selection_model_id"), table_name="llm_active_selection"
    )

    op.drop_table("llm_active_selection")

    op.drop_index(
        op.f("ix_large_language_model_model_id"), table_name="large_language_model"
    )

    op.drop_index(
        op.f("ix_large_language_model_served_by_org_id"),
        table_name="large_language_model",
    )

    op.drop_index(
        op.f("ix_large_language_model_made_by_org_id"),
        table_name="large_language_model",
    )

    op.drop_table("large_language_model")

    op.drop_index(op.f("ix_llm_org_role_org_id"), table_name="llm_org_role")

    op.drop_table("llm_org_role")

    op.drop_index(op.f("ix_llm_org_slug"), table_name="llm_org")

    op.drop_index(op.f("ix_llm_org_name"), table_name="llm_org")

    op.drop_table("llm_org")
