"""A document can be a schedule

Revision ID: 010
Revises: 009
Create Date: 2026-09-13 23:05:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

# Spelled out rather than imported from the models: a migration records
# what the schema WAS and became at this revision, and a later edit to
# the tuple must not silently rewrite history.
_KINDS = (
    "letter",
    "statement",
    "schedule",
    "form",
    "identification",
    "receipt",
    "other",
)
_WAS = tuple(kind for kind in _KINDS if kind != "schedule")


def _kind_check(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{kind}'" for kind in kinds) + ")"


def upgrade() -> None:
    """Paper that says what WILL happen, not what did.

    An amortization schedule is not a statement and not a letter: it is
    the lender's own projection of every future payment. Broad on
    purpose - a payment plan, a delivery schedule and an appointment
    schedule are the same kind of paper, and a kind narrow enough to
    name one lender's document is a kind nobody else can file under.

    A CHECK constraint cannot be altered in place on SQLite, so the
    table is rebuilt with the wider one; batch mode does the copy.
    """
    with op.batch_alter_table("document") as batch:
        batch.drop_constraint("ck_document_kind", type_="check")
        batch.create_check_constraint("ck_document_kind", _kind_check(_KINDS))


def downgrade() -> None:
    op.execute("UPDATE document SET kind = 'other' WHERE kind = 'schedule'")
    with op.batch_alter_table("document") as batch:
        batch.drop_constraint("ck_document_kind", type_="check")
        batch.create_check_constraint("ck_document_kind", _kind_check(_WAS))
