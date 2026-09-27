"""ai: voice models in the catalog (their mode, and how they bill)

Revision ID: 049
Revises: 048
Create Date: 2026-09-28 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None
aegis_stamp_signature = ("column", "llm_price", "input_cost_per_second")

VOICE_PRICES = (
    "input_cost_per_audio_token",
    "output_cost_per_audio_token",
    "input_cost_per_second",
    "output_cost_per_second",
    "input_cost_per_character",
)


def upgrade() -> None:
    with op.batch_alter_table("large_language_model", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "mode",
                sqlmodel.sql.sqltypes.AutoString(length=32),
                nullable=False,
                # Every row synced so far was synced as chat.
                server_default="chat",
            )
        )
        batch_op.create_index(
            batch_op.f("ix_large_language_model_mode"), ["mode"], unique=False
        )
    with op.batch_alter_table("llm_price", schema=None) as batch_op:
        for name in VOICE_PRICES:
            batch_op.add_column(sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("llm_price", schema=None) as batch_op:
        for name in reversed(VOICE_PRICES):
            batch_op.drop_column(name)
    with op.batch_alter_table("large_language_model", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_large_language_model_mode"))
        batch_op.drop_column("mode")
