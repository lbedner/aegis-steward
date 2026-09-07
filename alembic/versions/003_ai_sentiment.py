"""Conversation sentiment analysis results

Revision ID: 003
Revises: 002
Create Date: 2026-09-07 19:50:32.604237

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ai_sentiment service tables."""

    # Create sentiment_analysis table
    op.create_table(
        "sentiment_analysis",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("overall_sentiment", sa.String(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("assistant_performance", sa.String(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False, default=[]),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversation.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "overall_sentiment IN ('positive', 'neutral', 'negative', 'frustrated')",
            name="ck_sentiment_analysis_overall_sentiment",
        ),
        sa.CheckConstraint(
            "assistant_performance IN ('good', 'acceptable', 'poor')",
            name="ck_sentiment_analysis_assistant_performance",
        ),
    )

    op.create_index(
        op.f("ix_sentiment_analysis_conversation_id"),
        "sentiment_analysis",
        ["conversation_id"],
        unique=True,
    )


def downgrade() -> None:
    """Reverse ai_sentiment migration."""

    op.drop_index(
        op.f("ix_sentiment_analysis_conversation_id"), table_name="sentiment_analysis"
    )

    op.drop_table("sentiment_analysis")
