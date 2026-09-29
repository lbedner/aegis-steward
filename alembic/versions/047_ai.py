"""ai: which engine a live call runs on, on the voice profile

Revision ID: 047
Revises: 046
Create Date: 2026-09-27 23:30:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None
aegis_stamp_signature = ("column", "voice_profile", "live_engine")


def upgrade() -> None:
    with op.batch_alter_table("voice_profile", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "live_engine",
                sa.String(length=48),
                nullable=False,
                server_default="gpt-live",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("voice_profile", schema=None) as batch_op:
        batch_op.drop_column("live_engine")
