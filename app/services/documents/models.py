"""The document row: where the paper is, and what it is.

Bytes live in object storage under a key derived from their own SHA-256
(see ``app.core.storage``), so this table never holds a path and the same
scan uploaded twice is one row pointing at one object.

What a document MEANS - the case it belongs to, the deadline it creates,
the claim it proves - is deliberately absent. That is the consuming
application's business; this service stores paper and finds it again.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, Index
from sqlmodel import Field, SQLModel

from app.core.schema import one_of

# What kind of paper this is. Coarse on purpose: a fixed taxonomy would
# be the framework deciding what documents exist, which it should not.
# Anything finer rides tags.
DOCUMENT_KINDS = (
    "letter",
    "statement",
    # Paper that says what WILL happen rather than what did: an
    # amortization schedule, a payment plan, a delivery or appointment
    # schedule. Deliberately broad - a kind narrow enough to name one
    # lender's document is a kind nobody else can file anything under.
    "schedule",
    "form",
    # Issued once a year, by a payer, for a tax year - a 1098, a W-2. The
    # form says what its numbers mean, so it rides ``form_type`` rather
    # than a kind per form: a kind narrow enough to name one form is the
    # mistake ``schedule`` warns about. A Citizens 1098 filed as a
    # Statement because nothing closer existed (#138, 2026-09-14).
    "tax",
    "identification",
    "receipt",
    "other",
)


# The forms a ``tax`` document can be. Validated in the service rather
# than by a CHECK: the list grows every time a new form arrives, and a
# table rebuild per IRS form is a migration nobody should have to write.
TAX_FORMS = (
    "1098",
    "1098-E",
    "1098-T",
    "1099-B",
    "1099-DIV",
    "1099-G",
    "1099-INT",
    "1099-MISC",
    "1099-NEC",
    "1099-R",
    "1099-SA",
    "SSA-1099",
    "W-2",
    "1095-A",
    "1095-B",
    "1095-C",
    "5498",
    "5498-SA",
    "K-1",
)


def kind_label(document: Any) -> str:
    """What a reader calls this paper: its kind, or for tax paper the
    form and the year, which is what a tax document is looked for by."""
    if document.kind == "tax" and document.form_type:
        year = f" for {document.tax_year}" if document.tax_year else ""
        return f"{document.form_type}{year}"
    return document.kind


def kind_check() -> str:
    """The CHECK clause for ``DOCUMENT_KINDS``.

    Derived, not typed out again. The list lived twice - this tuple and
    the same strings hand-written into the constraint - and two copies
    of one truth is how a kind ends up legal in Python and rejected by
    the database. A migration is still needed to CHANGE the constraint
    (SQLite cannot alter one in place; Postgres drops and re-adds it),
    but the migration is then the only place the change is written.
    """
    return one_of("kind", DOCUMENT_KINDS)


def utcnow() -> datetime:
    """Naive UTC, matching the timestamp columns across services.

    A local-time timestamp reads differently depending on where the
    process runs, which makes "received on the 27th" a question about
    the server rather than about the document.
    """
    from datetime import UTC

    return datetime.now(UTC).replace(tzinfo=None)


class Document(SQLModel, table=True):
    """One stored document."""

    __tablename__ = "document"
    __table_args__ = (
        Index("ix_document_owner", "owner_user_id"),
        # The dedupe rule, enforced rather than merely checked: ingest
        # reads before it writes, and two concurrent uploads of the same
        # bytes can both miss that read. Partial, so a retired document
        # does not block re-filing the same paper later - the same shape
        # the soft-delete uniques elsewhere use.
        Index(
            "ix_document_owner_hash",
            "owner_user_id",
            "content_hash",
            unique=True,
            sqlite_where=Column("deleted_at").is_(None),
            postgresql_where=Column("deleted_at").is_(None),
        ),
        Index("ix_document_kind", "kind"),
        Index("ix_document_supersedes", "supersedes_id"),
        CheckConstraint(kind_check(), name="ck_document_kind"),
    )

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int | None = Field(default=None)
    title: str = Field(max_length=255)
    # What it was CALLED when it arrived, never touched afterwards. The
    # same shape as a transaction's raw descriptor beside its curated
    # payee: renaming a document used to overwrite the only copy of the
    # bank's own filename, and the storage key is a content hash, so
    # "enrollee-notices-flyer.pdf" stopped existing anywhere
    # (2026-09-19). Null on rows that predate the column.
    filename: str | None = Field(default=None, max_length=255)
    kind: str = Field(default="other", max_length=32)
    # Tax paper only (``kind == "tax"``): which form, and the year it is
    # FOR. A 1098 for 2025 is dated January 2026, so ``document_date``
    # cannot answer "what do I have for 2025".
    form_type: str | None = Field(default=None, max_length=16)
    tax_year: int | None = None
    # Where the bytes are. The key is content-derived, so it is portable
    # across backends; the backend name is recorded so a half-migrated
    # store still resolves every row.
    storage_key: str = Field(max_length=128)
    storage_backend: str = Field(default="filesystem", max_length=32)
    content_hash: str = Field(max_length=64)
    media_type: str | None = Field(default=None, max_length=128)
    byte_size: int = Field(default=0)
    page_count: int | None = None
    # When the DOCUMENT is dated (the letter's own date), as opposed to
    # when it arrived - a renewal request dated the 27th can land in
    # August's post.
    document_date: date | None = None
    received_at: datetime | None = None
    source: str = Field(default="upload", max_length=32)
    # How the paper reached you (mail, download, scan, email), as opposed
    # to ``source``, which is the mechanism that stored the bytes.
    channel: str | None = Field(default=None, max_length=32)
    # A newer version points at the one it replaces. The head of a chain
    # is the copy to cite; the rest stay reachable beneath it. Supersede,
    # never overwrite: the same rule facts follow.
    supersedes_id: int | None = Field(default=None, foreign_key="document.id")
    # One more gate on delete, and never auto-purged. Not an access tier.
    protected: bool = Field(default=False)
    note: str | None = None
    meta_data: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column("meta_data", JSON, nullable=False)
    )
    # The run that brought this document, when one did (migration
    # 012). Null is a real answer: a file dragged in arrived from
    # nobody, and the documents service is deliberately ignorant of
    # finance - the id is a plain column, never a foreign key.
    import_batch_id: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    def __repr__(self) -> str:
        return f"<Document id={self.id} title={self.title!r} kind={self.kind}>"


class DocumentTag(SQLModel, table=True):
    """A free-form label on a document.

    Tags rather than a taxonomy: what counts as a meaningful category
    differs per application, and the framework has no business guessing.
    """

    __tablename__ = "document_tag"
    __table_args__ = (
        Index("ix_document_tag_document", "document_id"),
        Index("uq_document_tag", "document_id", "label", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id")
    label: str = Field(max_length=64)


class DocumentPage(SQLModel, table=True):
    """One page of a document as extraction read it.

    ``method`` says how the text was obtained (the PDF's own text layer,
    a vision model over the rendered page, or none), so any claim built
    on it later can cite both the page and the way it was read. A page
    that could not be read is a row with ``status='unread'`` and the
    reason in ``detail``, never an absence.
    """

    __tablename__ = "document_page"
    __table_args__ = (
        Index("ix_document_page_document", "document_id"),
        Index("uq_document_page", "document_id", "page_number", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id")
    page_number: int
    status: str = Field(default="unread", max_length=16)
    method: str = Field(default="none", max_length=16)
    text: str | None = None
    # The rendered page in object storage, what the thumbnail strip shows.
    image_key: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    detail: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime | None = None
