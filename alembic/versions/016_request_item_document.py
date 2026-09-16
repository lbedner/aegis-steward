"""The paper that answers one item

Revision ID: 016
Revises: 015
Create Date: 2026-09-15 02:30:00.000000

"A copy of the power of attorney" is answered by a document, and the
answer is worth nothing if you cannot open it from the line that asked.
So the item carries the document that satisfies it - on the ITEM, not
the request, because one letter's three demands are answered by three
different pieces of paper on three different days.

A plain column, never a foreign key: the documents service says what a
document MEANS is the consuming application's business, so the
reference points one way only - the same call ``document_party`` made.
"""

import sqlalchemy as sa

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_item", sa.Column("document_id", sa.Integer(), nullable=True)
    )
    op.create_index("ix_request_item_document", "request_item", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_request_item_document", table_name="request_item")
    with op.batch_alter_table("request_item") as batch:
        batch.drop_column("document_id")
