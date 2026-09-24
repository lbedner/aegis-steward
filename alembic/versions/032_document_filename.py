"""document.filename: what a document was called when it arrived

Renaming a document overwrote its title, and the filename stopped
existing anywhere - the storage key is a content hash and the metadata
is empty. The same shape as a transaction's raw descriptor beside its
curated payee: the original is kept, the name is ours.

The originals of the documents already renamed are recovered here, out
of the approval cards that renamed them: each froze its display rows,
and the Title row reads "<what it was> -> <what it became>". Only where
the recovered value still looks like a filename, and only into a NULL -
a carry-over that guesses is worse than a column that starts empty.

Revision ID: 032
Revises: 031
"""

from collections.abc import Sequence
import json
import re

import sqlalchemy as sa

from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# "Aug-2026.pdf -> GreenSky Application - page 1: GREENSKY", with the
# arrow the cards actually carry.
_RENAME = re.compile(r"^(?P<was>.+?)\s*(?:→|->)\s")
_EXTENSION = re.compile(r"\.[a-z0-9]{2,5}$", re.I)


def upgrade() -> None:
    op.add_column(
        "document", sa.Column("filename", sa.String(length=255), nullable=True)
    )
    _carry_over()


def _carry_over() -> None:
    """Fill the column from the cards that did the renaming."""
    bind = op.get_bind()
    cards = bind.execute(
        sa.text(
            "SELECT payload, result FROM finance_pending_change "
            "WHERE change_type = 'document.metadata' AND status = 'approved'"
        )
    ).fetchall()
    for payload, result in cards:
        said = _loaded(payload)
        frozen = _loaded(result)
        document_id = said.get("document_id")
        if not document_id:
            continue
        for row in frozen.get("display") or []:
            if row.get("label") != "Title":
                continue
            match = _RENAME.match(str(row.get("value") or ""))
            was = match.group("was").strip() if match else ""
            if not was or not _EXTENSION.search(was):
                continue
            bind.execute(
                sa.text(
                    "UPDATE document SET filename = :was "
                    "WHERE id = :id AND filename IS NULL"
                ),
                {"was": was, "id": document_id},
            )


def _loaded(value: object) -> dict:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def downgrade() -> None:
    op.drop_column("document", "filename")
