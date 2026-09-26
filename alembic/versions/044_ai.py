"""ai: a live call's dead-air limit on the voice profile

Revision ID: 044
Revises: 043
Create Date: 2026-09-26 04:40:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None
aegis_stamp_signature = ("column", "voice_profile", "live_idle_seconds")


def upgrade() -> None:
    with op.batch_alter_table("voice_profile", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "live_idle_seconds", sa.Integer(), nullable=False, server_default="30"
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("voice_profile", schema=None) as batch_op:
        batch_op.drop_column("live_idle_seconds")
