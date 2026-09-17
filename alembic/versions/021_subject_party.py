"""Whose money, and who that is in the address book

Revision ID: 021
Revises: 020
Create Date: 2026-09-15 06:00:00.000000

``finance_subject`` says whose money a row describes; ``party`` says who
somebody is. They were the same person twice - James Bedner the subject
of a Medicaid renewal and James Bedner whose pension account this is -
and a pick-list that offers both is a pick-list nobody can answer.

So the subject points at the party. Creating it stays the app's job: a
person becomes a subject the moment an account is put in their name,
not by somebody maintaining a second list.

A plain column, like every other party reference in this milestone: a
party is soft-deleted and the ledger still has to say whose money it
was.
"""

import sqlalchemy as sa

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("finance_subject", sa.Column("party_id", sa.Integer(), nullable=True))
    op.create_index("ix_finance_subject_party", "finance_subject", ["party_id"])


def downgrade() -> None:
    op.drop_index("ix_finance_subject_party", table_name="finance_subject")
    with op.batch_alter_table("finance_subject") as batch:
        batch.drop_column("party_id")
