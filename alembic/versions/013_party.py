"""One table for the people and organizations everything else points at

Revision ID: 013
Revises: 012
Create Date: 2026-09-15 00:20:00.000000

The app has exactly one notion of a person - ``owner_user_id``, the user
- so a case's subject, the agency that wrote to you, the facility
holding an account and the attorney copied on the letter cannot be told
apart or named at all.

People and organizations share the table because the difference between
them is a field, not a schema. Roles are absent on purpose: a facility
is the care provider in one matter and a payee in the ledger, so a role
belongs to a relationship rather than to the party.
"""

import sqlalchemy as sa

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None

# Spelled out rather than imported from the model: a migration records
# what the schema BECAME at this revision, and a later edit to the tuple
# must not silently rewrite history.
_KINDS = ("person", "organization")


def upgrade() -> None:
    op.create_table(
        "party",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sort_name", sa.String(length=255), nullable=False),
        sa.Column("contact", sa.JSON(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in _KINDS) + ")",
            name="ck_party_kind",
        ),
    )
    op.create_index("ix_party_owner", "party", ["owner_user_id"])
    op.create_index("ix_party_kind", "party", ["kind"])
    op.create_index("ix_party_sort_name", "party", ["sort_name"])
    op.create_index("ix_party_deleted", "party", ["deleted_at"])


def downgrade() -> None:
    op.drop_table("party")
