"""mail_batch, mail_message, mail_attachment: mail that arrived as a file

Nothing in this app read mail. Comms sends it and that is all. This is
the half of the mail milestone that happens after the messages exist,
and it needs no connection: a Google Takeout .mbox or a saved .eml is
uploaded like any other document, the sender is matched to a contact by
address, and every attachment becomes paper on the shelf - which
already names, dates and files it.

The connection (MI-01, MI-02) moved to the end of the milestone once
its cost was known: every way of reading Gmail is a restricted scope,
a project in Testing mode issues refresh tokens that die in 7 days, and
somebody has to register a client. None of that blocks reading an
export.

Three tables, the import pipeline's shape: a batch per file with counts
and a sha so the same bytes are answered with the batch that read them;
a message per Message-ID, which RFC 5322 makes unique, so an overlapping
re-export lands each one once; and a link from message to document,
because one statement can arrive on two messages and the shelf holds it
once. owner_user_id is nullable with no FK: AUTH_ENABLED is false here.

Revision ID: 033
Revises: 032
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_batch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("messages_total", sa.Integer(), nullable=False),
        sa.Column("messages_new", sa.Integer(), nullable=False),
        sa.Column("messages_duplicate", sa.Integer(), nullable=False),
        sa.Column("attachments_filed", sa.Integer(), nullable=False),
        sa.Column("attachments_duplicate", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('processing', 'done', 'failed')", name="ck_mail_batch_status"
        ),
    )
    # Not per owner: owner_user_id is NULL here, and NULLs never collide in
    # a SQLite unique index, so a composite key would enforce nothing.
    op.create_index("uq_mail_batch_file", "mail_batch", ["file_sha256"], unique=True)

    op.create_table(
        "mail_message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        # 998 is RFC 5322's line limit; a Message-ID cannot be longer.
        sa.Column("message_id", sa.String(length=998), nullable=False),
        sa.Column("from_address", sa.String(length=320), nullable=False),
        sa.Column("from_name", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=998), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("party_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["batch_id"], ["mail_batch.id"]),
        sa.ForeignKeyConstraint(["party_id"], ["party.id"]),
    )
    op.create_index("uq_mail_message_id", "mail_message", ["message_id"], unique=True)
    op.create_index("ix_mail_message_batch", "mail_message", ["batch_id"])
    op.create_index("ix_mail_message_party", "mail_message", ["party_id"])
    op.create_index("ix_mail_message_sent", "mail_message", ["sent_at"])

    op.create_table(
        "mail_attachment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["message_id"], ["mail_message.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"]),
    )
    op.create_index(
        "uq_mail_attachment",
        "mail_attachment",
        ["message_id", "document_id"],
        unique=True,
    )
    op.create_index("ix_mail_attachment_document", "mail_attachment", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_mail_attachment_document", table_name="mail_attachment")
    op.drop_index("uq_mail_attachment", table_name="mail_attachment")
    op.drop_table("mail_attachment")
    for name in (
        "ix_mail_message_sent",
        "ix_mail_message_party",
        "ix_mail_message_batch",
        "uq_mail_message_id",
    ):
        op.drop_index(name, table_name="mail_message")
    op.drop_table("mail_message")
    op.drop_index("uq_mail_batch_file", table_name="mail_batch")
    op.drop_table("mail_batch")
