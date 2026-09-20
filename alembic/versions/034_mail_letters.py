"""mail_message.document_id, mail_batch.letters_filed: the message is a letter

Half of what an agency sends has no attachment: the county emails the
renewal reminder in the body, the insurer emails the EOB as text. That
mail IS the letter, and 033 kept the attachment and threw away the
letter that explains it. The first real email through the door proved
it - Optum's statement notice had no statement in it, only a body naming
the bank, a phone and a website, which the identity rules read off a
page as readily as off a scan.

The body becomes a document of kind letter with page 1 already read
(method 'mail'), the subject as its title and the message as its
provenance. mail_message points at it; the batch counts them.

Revision ID: 034
Revises: 033
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "034"
down_revision: str | None = "033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A default for the rows already there: 033 has run on a real
    # database, and a NOT NULL column cannot land on them without one.
    op.add_column(
        "mail_batch",
        sa.Column("letters_filed", sa.Integer(), nullable=False, server_default="0"),
    )
    # Batch mode: SQLite cannot add a foreign key to a table in place.
    with op.batch_alter_table("mail_message") as batch_op:
        batch_op.add_column(sa.Column("document_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_mail_message_document", "document", ["document_id"], ["id"]
        )
        batch_op.create_index("ix_mail_message_document", ["document_id"])


def downgrade() -> None:
    with op.batch_alter_table("mail_message") as batch_op:
        batch_op.drop_index("ix_mail_message_document")
        batch_op.drop_constraint("fk_mail_message_document", type_="foreignkey")
        batch_op.drop_column("document_id")
    op.drop_column("mail_batch", "letters_filed")
