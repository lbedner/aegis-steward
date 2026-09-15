"""An answer that knows where it came from

Revision ID: 017
Revises: 016
Create Date: 2026-09-15 03:30:00.000000

The answer to a request item is a claim about someone's money at a
moment - "gross pension income for James on 1 August 2026 was $X" - and
what makes it fit to file is not the number, it is knowing where the
number came from. A real session proved it: the ledger showed a
$2,075.00 deposit where the form asked for GROSS, which is the deposit
plus the premium withheld before it ever arrived. Both numbers are
useful; only one is an answer.

So: subject, attribute, value, as-of date, and provenance - stated,
document (with the page), or ledger. Two facts for the same subject,
attribute and date are deliberate: recording that a deposit and a
benefit letter disagree is the feature, and deciding which is right is
the reader's. A correction SUPERSEDES rather than overwrites, so what
was filed last year is still recoverable.

``period`` exists because a pension portal quotes a daily rate and the
county asks for a monthly figure. The rate is stored as quoted; turning
it into a month is arithmetic done at the point of reading, never filed
as a quotation.
"""

import sqlalchemy as sa

from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("subject_party_id", sa.Integer(), nullable=False),
        sa.Column("matter_id", sa.Integer(), nullable=True),
        sa.Column("attribute", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("value_cents", sa.Integer(), nullable=True),
        sa.Column("period", sa.String(length=10), nullable=False),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("provenance", sa.String(length=16), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    for name, cols in (
        ("ix_fact_subject", ["subject_party_id"]),
        ("ix_fact_matter", ["matter_id"]),
        ("ix_fact_attribute", ["attribute"]),
        ("ix_fact_document", ["document_id"]),
        ("ix_fact_deleted", ["deleted_at"]),
    ):
        op.create_index(name, "fact", cols)


def downgrade() -> None:
    op.drop_table("fact")
