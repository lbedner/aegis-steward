"""What kind of ask it is, and which asks are alternatives

Revision ID: 024
Revises: 023
Create Date: 2026-09-15 14:00:00.000000

A real letter asked for four things, and a list that drew them
identically hid the two facts that decide the afternoon.

``kind`` - find a document, fill in a form, record a figure, do
something. Those are different work and the row should say which.

``option_group`` - the county will take the power of attorney OR a
signed designation OR an attestation of incapacity. Three rows that
each read as mandatory describe a harder afternoon than the one you
have, and closing one leaves two demands standing that nobody owes.
Items sharing a group are alternatives: any one settles it.
"""

import sqlalchemy as sa

from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_item",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="document"),
    )
    op.add_column(
        "request_item", sa.Column("option_group", sa.String(length=40), nullable=True)
    )
    op.create_index("ix_request_item_group", "request_item", ["option_group"])


def downgrade() -> None:
    op.drop_index("ix_request_item_group", table_name="request_item")
    with op.batch_alter_table("request_item") as batch:
        batch.drop_column("option_group")
        batch.drop_column("kind")
