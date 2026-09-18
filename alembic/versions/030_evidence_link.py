"""evidence_link: what answers an ask, and how

Evidence and answers are many-to-many in both directions.
``request_item.document_id`` held ONE document, so "one statement
satisfies three asks" worked by accident and "one ask needs three
letters" was impossible.

The old column stays for now. Dropping it is a second migration once
nothing reads it, because a column removed in the same change that adds
its replacement has no rollback that keeps the data.

Revision ID: 030
Revises: 029
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_link",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_item_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("fact_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_link_item", "evidence_link", ["request_item_id"])
    op.create_index("ix_evidence_link_document", "evidence_link", ["document_id"])
    op.create_index("ix_evidence_link_fact", "evidence_link", ["fact_id"])
    op.create_index(
        "uq_evidence_link",
        "evidence_link",
        ["request_item_id", "document_id", "page", "fact_id"],
        unique=True,
    )

    # Carry the existing attachments across, so an ask already answered
    # stays answered. Page unknown: the column never held one.
    op.execute(
        """
        INSERT INTO evidence_link
            (request_item_id, document_id, page, fact_id, note, created_at)
        SELECT id, document_id, NULL, NULL, NULL, CURRENT_TIMESTAMP
        FROM request_item
        WHERE document_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("uq_evidence_link", table_name="evidence_link")
    op.drop_index("ix_evidence_link_fact", table_name="evidence_link")
    op.drop_index("ix_evidence_link_document", table_name="evidence_link")
    op.drop_index("ix_evidence_link_item", table_name="evidence_link")
    op.drop_table("evidence_link")
