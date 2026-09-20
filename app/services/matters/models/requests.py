"""What was asked for, and what answers it.

Split out of the matters model file at the size budget, and the seam
is a real one: a request is a DEMAND with a deadline, its items are
the individual asks, and an evidence link is what satisfies one. A
party, a matter and a fact are all longer-lived than any of these.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.core.clock import utcnow

REQUEST_STATUSES = ("open", "satisfied", "waived")

# What KIND of work an ask is. A letter asking for a power of attorney,
# for a form to be filled in, and for a figure as of a date is asking
# for three different afternoons, and a list that draws them the same
# way makes the reader work out which is which every time.
ITEM_KINDS = (
    ("document", "Find a document"),
    ("form", "Fill in a form"),
    ("figure", "Record a figure"),
    ("action", "Do something"),
)
ITEM_STATUSES = ("needed", "satisfied", "not_applicable", "waived")


class EvidenceLink(SQLModel, table=True):
    """What answers an ask, and how. See ``matters/evidence.py``.

    Many-to-many both ways, so a row rather than a column. Exactly one
    of ``document_id`` or ``fact_id``; neither is an FK, for the reason
    the rest of this file gives.
    """

    __tablename__ = "evidence_link"
    __table_args__ = (
        Index("ix_evidence_link_item", "request_item_id"),
        Index("ix_evidence_link_document", "document_id"),
        Index("ix_evidence_link_fact", "fact_id"),
        # Two PAGES is two links; the same page twice is one.
        Index(
            "uq_evidence_link",
            "request_item_id",
            "document_id",
            "page",
            "fact_id",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    request_item_id: int = Field()
    document_id: int | None = Field(default=None)
    page: int | None = Field(default=None)
    fact_id: int | None = Field(default=None)
    note: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class Request(SQLModel, table=True):
    """What one letter obliges you to produce, and by when.

    An obligation cannot be a field on the document. One page of the
    Medicaid renewal carried three separate demands - a power of
    attorney, proof of GROSS income for two named pensions, resource
    values as of a date - under a single deadline, each satisfiable by
    different evidence at different times.

    So: one document, many requests, resolved independently.
    """

    __tablename__ = "request"
    __table_args__ = (
        Index("ix_request_matter", "matter_id"),
        Index("ix_request_document", "document_id"),
        Index("ix_request_due", "due_on"),
        Index("ix_request_status", "status"),
        Index("ix_request_deleted", "deleted_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int | None = Field(default=None)
    matter_id: int = Field()
    # The letter this came off, when there is one. A request typed from
    # a phone call is still a request.
    document_id: int | None = Field(default=None)
    requester_party_id: int | None = Field(default=None)

    received_on: date | None = Field(default=None)
    due_on: date | None = Field(default=None)
    status: str = Field(default="open", max_length=16)
    note: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    deleted_at: datetime | None = Field(default=None)


class RequestItem(SQLModel, table=True):
    """One demand inside a request, markable on its own.

    ``asked`` is the sentence AS WRITTEN, because what the county
    actually said is the thing you are held to and the thing you will
    read back when arguing. ``ask`` is our normalisation of it - what
    would satisfy this - and the two are kept apart on purpose: a
    paraphrase that replaces the original loses the only wording that
    matters.
    """

    __tablename__ = "request_item"
    __table_args__ = (
        Index("ix_request_item_request", "request_id"),
        Index("ix_request_item_status", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    request_id: int = Field()
    ordinal: int = Field(default=0)

    asked: str = Field()
    ask: str | None = Field(default=None, max_length=255)
    subject_party_id: int | None = Field(default=None)
    # "as of 1 August 2026" - a resource value is only an answer on the
    # date it was asked about.
    as_of: date | None = Field(default=None)

    kind: str = Field(default="document", max_length=16)
    # Items sharing a group are ALTERNATIVES: the county will take any
    # one of the POA, the designation form or the attestation, and three
    # rows that each read as mandatory describe a harder afternoon than
    # the one you actually have.
    option_group: str | None = Field(default=None, max_length=40)
    status: str = Field(default="needed", max_length=20)
    resolution: str | None = Field(default=None)
    # The paper that answers this item. A plain column, never an FK:
    # the documents service says what a document MEANS is the consuming
    # application's business, so the reference points one way only.
    document_id: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
