"""ai: live engines as table data

Revision ID: 048
Revises: 047
Create Date: 2026-09-28 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None
aegis_stamp_signature = ("table", "live_engine")


def upgrade() -> None:
    op.create_table(
        "live_engine",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sqlmodel.sql.sqltypes.AutoString(length=48), nullable=False),
        sa.Column("llm_id", sa.Integer(), nullable=False),
        sa.Column(
            "transport", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False
        ),
        sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("warning", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("instructions", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["llm_id"], ["large_language_model.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    with op.batch_alter_table("live_engine", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_live_engine_key"), ["key"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_live_engine_llm_id"), ["llm_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("live_engine", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_live_engine_llm_id"))
        batch_op.drop_index(batch_op.f("ix_live_engine_key"))
    op.drop_table("live_engine")
