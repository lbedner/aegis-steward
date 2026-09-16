"""The place a fact was read off, and the one a sign-in gets you into

Revision ID: 020
Revises: 019
Create Date: 2026-09-15 05:10:00.000000

A pension portal is not a string. Typed as free text it is spelled
three ways across three facts and matches nothing; as a row it is the
same organization the matter already names as a participant, with the
website on it.

So both point at ``party``: the fact at the place its figure came from,
the sign-in at the place it gets you into - which is a DIFFERENT party
from the one whose login it is. James's account at the IBEW fund is one
row naming both.

Plain columns rather than foreign keys, matching every other party
reference in this milestone: a party is soft-deleted, and a fact that
survives the tidying of an address book is the point of recording it.
"""

import sqlalchemy as sa

from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fact", sa.Column("source_party_id", sa.Integer(), nullable=True))
    op.create_index("ix_fact_source_party", "fact", ["source_party_id"])
    op.add_column("sign_in", sa.Column("site_party_id", sa.Integer(), nullable=True))
    op.create_index("ix_sign_in_site", "sign_in", ["site_party_id"])


def downgrade() -> None:
    op.drop_index("ix_sign_in_site", table_name="sign_in")
    op.drop_index("ix_fact_source_party", table_name="fact")
    with op.batch_alter_table("sign_in") as batch:
        batch.drop_column("site_party_id")
    with op.batch_alter_table("fact") as batch:
        batch.drop_column("source_party_id")
