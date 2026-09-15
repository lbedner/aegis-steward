"""What a letter obliges you to produce, and by when

Revision ID: 015
Revises: 014
Create Date: 2026-09-15 01:30:00.000000

An obligation cannot be a field on the document. One page of the
Medicaid renewal carried three separate demands - a power of attorney,
proof of GROSS income for two named pensions, resource values as of a
date - under a single deadline, each satisfiable by different evidence
at different times. So: one document, many requests, resolved
independently, each with items markable on their own.

``asked`` holds the sentence as written and ``ask`` our normalisation of
it, kept apart because a paraphrase that replaces the original loses the
only wording you are actually held to.

There is no overdue column. Overdue is a reading of the due date and the
clock; a stored flag is wrong from the first midnight after it is
written.
"""

import sqlalchemy as sa

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("matter_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("requester_party_id", sa.Integer(), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    for name, cols in (
        ("ix_request_matter", ["matter_id"]),
        ("ix_request_document", ["document_id"]),
        ("ix_request_due", ["due_on"]),
        ("ix_request_status", ["status"]),
        ("ix_request_deleted", ["deleted_at"]),
    ):
        op.create_index(name, "request", cols)

    op.create_table(
        "request_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("asked", sa.Text(), nullable=False),
        sa.Column("ask", sa.String(length=255), nullable=True),
        sa.Column("subject_party_id", sa.Integer(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_request_item_request", "request_item", ["request_id"])
    op.create_index("ix_request_item_status", "request_item", ["status"])


def downgrade() -> None:
    op.drop_table("request_item")
    op.drop_table("request")
