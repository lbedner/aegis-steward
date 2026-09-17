"""One organization, not two rows of it

Revision ID: 022
Revises: 021
Create Date: 2026-09-15 10:00:00.000000

The NYSLRS is in the address book as a party - with its website, its
sign-in, and its part in a Medicaid matter - and the ledger has its own
notion of who an account is held WITH. Left alone, naming it on an
account types it a second time and the two drift: a website on one, a
logo on the other, and nothing saying they are the same body.

So the institution points at the party. The ledger keeps its own row
because it carries things a party has no business knowing - a provider
id, a logo, whether the bank uses tokenized account numbers - but
identity is the address book's.

A plain column, like every other party reference in this milestone.
"""

import sqlalchemy as sa

from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_institution", sa.Column("party_id", sa.Integer(), nullable=True)
    )
    op.create_index("ix_finance_institution_party", "finance_institution", ["party_id"])


def downgrade() -> None:
    op.drop_index("ix_finance_institution_party", table_name="finance_institution")
    with op.batch_alter_table("finance_institution") as batch:
        batch.drop_column("party_id")
