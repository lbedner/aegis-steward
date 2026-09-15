"""The case a document belongs to

Revision ID: 014
Revises: 013
Create Date: 2026-09-15 00:55:00.000000

Letters from an agency are episodes in a relationship with a case
number, a subject and a status. Without something to attach them to,
the next letter is an orphan and the last one is unfindable.

Roles ride the LINKS, never the party: ``matter_participant`` says what
somebody is to this case, ``document_party`` says what they are to one
letter, and the same party can be the facility in a matter and a payee
in the ledger without either fact touching the other.

``document_party.document_id`` is a plain column. The documents service
stores paper and is deliberately ignorant of what it means, so the
reference points one way only.
"""

import sqlalchemy as sa

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

_STATUSES = ("open", "closed")


def upgrade() -> None:
    op.create_table(
        "matter",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=True),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column("subject_party_id", sa.Integer(), nullable=True),
        sa.Column("counterpart_party_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=True),
        sa.Column("closed_on", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in _STATUSES) + ")",
            name="ck_matter_status",
        ),
    )
    for name, cols in (
        ("ix_matter_owner", ["owner_user_id"]),
        ("ix_matter_status", ["status"]),
        ("ix_matter_subject", ["subject_party_id"]),
        ("ix_matter_reference", ["reference"]),
        ("ix_matter_deleted", ["deleted_at"]),
    ):
        op.create_index(name, "matter", cols)

    op.create_table(
        "matter_participant",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("matter_id", sa.Integer(), nullable=False),
        sa.Column("party_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_matter_participant_matter", "matter_participant", ["matter_id"])
    op.create_index("ix_matter_participant_party", "matter_participant", ["party_id"])
    op.create_index(
        "uq_matter_participant",
        "matter_participant",
        ["matter_id", "party_id", "role"],
        unique=True,
    )

    op.create_table(
        "document_party",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("party_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_document_party_document", "document_party", ["document_id"])
    op.create_index("ix_document_party_party", "document_party", ["party_id"])
    op.create_index(
        "uq_document_party",
        "document_party",
        ["document_id", "party_id", "role"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("document_party")
    op.drop_table("matter_participant")
    op.drop_table("matter")
