"""finance: one payee key, stored on each row with its version (#542)

Revision ID: 066
Revises: 065
Create Date: 2026-10-20 21:00:00.000000

"""

import re
import unicodedata

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "066"
down_revision = "065"
branch_labels = None
depends_on = None
aegis_stamp_signature = ("column", "finance_transaction", "payee_key")


def _first_four(
    merchant_name: str | None, wording: str | None, name: str | None
) -> str:
    """The key every row was grouped by before this one, frozen here: the
    first four words of what it said. Stored as version 0, so the
    recompute knows what each budget line keyed on it now means."""
    raw = merchant_name or wording or name or ""
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    words = re.sub(r"[^0-9A-Za-z\s]", " ", folded).upper().split()
    return " ".join(words[:4])


def upgrade() -> None:
    with op.batch_alter_table("finance_transaction", schema=None) as batch_op:
        batch_op.add_column(sa.Column("payee_key", sa.String(191), nullable=True))
        batch_op.add_column(sa.Column("payee_key_version", sa.Integer(), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, merchant_name, original_description, name"
            " FROM finance_transaction"
        )
    ).all()
    if rows:
        bind.execute(
            sa.text(
                "UPDATE finance_transaction SET payee_key = :key,"
                " payee_key_version = 0 WHERE id = :id"
            ),
            [{"id": r[0], "key": _first_four(r[1], r[2], r[3])} for r in rows],
        )


def downgrade() -> None:
    with op.batch_alter_table("finance_transaction", schema=None) as batch_op:
        batch_op.drop_column("payee_key_version")
        batch_op.drop_column("payee_key")
