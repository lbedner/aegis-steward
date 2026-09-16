"""How you get into someone's account

Revision ID: 019
Revises: 018
Create Date: 2026-09-15 04:30:00.000000

Managing somebody's affairs means logging in as them - the pension
portal the income figure came off, the facility's billing page, the
county's upload site. The thing this replaces is a sticky note beside
the laptop.

The secret is stored AES-256-GCM (``app.core.encryption``), bound to
its own row, so a stolen database dump is not a stolen set of logins.
The running app holds the key; this protects the dump, not the machine,
and the column name says outright what is in it.
"""

import sqlalchemy as sa

from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sign_in",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("party_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sign_in_party", "sign_in", ["party_id"])
    op.create_index("ix_sign_in_deleted", "sign_in", ["deleted_at"])


def downgrade() -> None:
    op.drop_table("sign_in")
