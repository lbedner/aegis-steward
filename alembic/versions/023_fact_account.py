"""A fact about an account, and the number an account is known by

Revision ID: 023
Revises: 022
Create Date: 2026-09-15 12:00:00.000000

Two columns for the same problem: a pension page that knew whose money
it was and who it was with, and had nowhere to put anything the pension
actually SAYS.

``fact.account_id`` - "$1,004.93 a month", "plan A15", "single life
allowance option 0" are claims about the account that pays them, not
only about the man they pay. Without it every fact about a person
appeared on every account of theirs, which is how an incidental balance
at a nursing home ends up on a pension page.

``finance_account.reference`` - the number the institution prints: a
NYSLRS ID, a policy number, a member number. The same call a matter
makes about an agency's case number, for the same reason: it is what
two documents agree about and what somebody types into a search box.
``mask`` is four digits of a card and was never this.
"""

import sqlalchemy as sa

from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fact", sa.Column("account_id", sa.Integer(), nullable=True))
    op.create_index("ix_fact_account", "fact", ["account_id"])
    # No schema argument: the finance tables are schema-qualified on
    # Postgres and bare on SQLite, and every finance migration here adds
    # its columns by bare name for exactly that reason.
    op.add_column(
        "finance_account", sa.Column("reference", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("finance_account", "reference")
    op.drop_index("ix_fact_account", table_name="fact")
    with op.batch_alter_table("fact") as batch:
        batch.drop_column("account_id")
