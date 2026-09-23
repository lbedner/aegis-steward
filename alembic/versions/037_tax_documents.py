"""A document can be tax paper, for a form and a year

A Citizens 1098 filed as a Statement because that was the closest of
the kinds (#138). Tax paper is issued once a year, by a payer, for a tax
year that is not the date printed on it, and the form says what its
numbers mean: one ``tax`` kind, plus ``form_type`` and ``tax_year``.

Revision ID: 037
Revises: 036
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "037"
down_revision: str | None = "036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled out rather than imported from the models: a migration records
# what the schema WAS and became at this revision.
_WAS = (
    "letter",
    "statement",
    "schedule",
    "form",
    "identification",
    "receipt",
    "other",
)
_KINDS = (*_WAS[:4], "tax", *_WAS[4:])


def _kind_check(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{kind}'" for kind in kinds) + ")"


def upgrade() -> None:
    # Batch mode: SQLite cannot alter a CHECK constraint in place.
    with op.batch_alter_table("document") as batch:
        batch.add_column(sa.Column("form_type", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("tax_year", sa.Integer(), nullable=True))
        batch.drop_constraint("ck_document_kind", type_="check")
        batch.create_check_constraint("ck_document_kind", _kind_check(_KINDS))


def downgrade() -> None:
    op.execute("UPDATE document SET kind = 'statement' WHERE kind = 'tax'")
    with op.batch_alter_table("document") as batch:
        batch.drop_constraint("ck_document_kind", type_="check")
        batch.create_check_constraint("ck_document_kind", _kind_check(_WAS))
        batch.drop_column("tax_year")
        batch.drop_column("form_type")
