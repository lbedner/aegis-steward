"""The party: one row for every person and organization anything points at.

The app has exactly one notion of a person today - ``owner_user_id``,
the user - so a case's subject, the agency that wrote to you, the
facility holding an account and the attorney copied on the letter cannot
be told apart or named at all.

One table for people AND organizations, because the difference between
them is a field, not a schema: both are named, both are written to, both
get pointed at from the same places.

Roles are deliberately NOT here. A nursing facility is the care provider
in one matter and a payee in the ledger; an agency is a correspondent
here and a payer somewhere else. A role belongs to a relationship, the
way ``llm_org_role`` records what an organization is to a model rather
than baking it into the organization.

Identity is the row, never the name. Two "Bedner" parties are two
people, and merging them is a deliberate act rather than something a
normalizer does by accident on a Tuesday.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, Index
from sqlmodel import Field, SQLModel

PARTY_KINDS = ("person", "organization")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def kind_check() -> str:
    """The CHECK clause for ``PARTY_KINDS``, spelled from the tuple so the
    constraint cannot drift from the values the app writes."""
    allowed = ", ".join(f"'{kind}'" for kind in PARTY_KINDS)
    return f"kind IN ({allowed})"


class Party(SQLModel, table=True):
    """A person or an organization, named once and pointed at often."""

    __tablename__ = "party"
    __table_args__ = (
        CheckConstraint(kind_check(), name="ck_party_kind"),
        Index("ix_party_owner", "owner_user_id"),
        Index("ix_party_kind", "kind"),
        Index("ix_party_sort_name", "sort_name"),
        Index("ix_party_deleted", "deleted_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int | None = Field(default=None)

    kind: str = Field(max_length=16)
    # What to call them on screen: "Eleanor Nursing Care Center",
    # "James Bedner". Written as a person would write it.
    name: str = Field(max_length=255)
    # What to sort them by, which is not the same string: "Bedner, James"
    # files where a reader looks for it. Derived on write when nobody
    # says otherwise, never derived on READ - a list that sorts by a
    # value it computed cannot be searched by that value.
    sort_name: str = Field(max_length=255)

    # Address, phone, email - one JSON block rather than six columns.
    # Contact details are a bag whose shape differs per party (a county
    # office has a PO box and a fax; a person has a mobile), and every
    # column added for one of them is null for all the others.
    contact: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    note: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    # Soft delete: a party is pointed at by matters and documents, and a
    # hard delete would take the meaning of those rows with it.
    deleted_at: datetime | None = Field(default=None)


def sort_name_for(name: str, kind: str) -> str:
    """Where this party files, when nobody has said.

    A person files under their last name and an organization under its
    own, so "James Bedner" becomes "Bedner, James" and "Dutchess County
    DSS" stays put. One rule, and a guess a human can overrule - the
    column is stored, not computed, precisely so they can.
    """
    cleaned = " ".join((name or "").split())
    if kind != "person" or " " not in cleaned:
        return cleaned
    first, _, last = cleaned.rpartition(" ")
    return f"{last}, {first}"


MATTER_STATUSES = ("open", "closed")

# What a party IS to one matter. Deliberately a small, open list: the
# roles a case has are the roles that case has, and a taxonomy narrow
# enough to be correct for Medicaid is wrong for an estate.
PARTICIPANT_ROLES = (
    "subject",
    "representative",
    "agency",
    "facility",
    "counsel",
    "other",
)

# What a party is to one DOCUMENT, which is a different question from
# what they are to the matter: the agency that WROTE the letter is the
# same agency that is the matter's counterpart, and the letter is about
# the subject rather than from them.
DOCUMENT_PARTY_ROLES = ("sender", "subject", "about")


def status_check() -> str:
    allowed = ", ".join(f"'{s}'" for s in MATTER_STATUSES)
    return f"status IN ({allowed})"


class Matter(SQLModel, table=True):
    """A case: the relationship a letter is an episode of.

    Letters from an agency are not one-offs. Without something to attach
    them to, the next one is an orphan and the last one is unfindable.

    ``reference`` is the agency's OWN number (MA258760XX), not ours: it
    is how the case is named in every letter, on the phone, and on the
    form - so it is what somebody will search for.

    No workflow engine. ``status`` is open or closed and nothing else;
    what must happen next is a request (ST-05), because a status that
    tries to say it drifts out of step with the letters that decide it.
    """

    __tablename__ = "matter"
    __table_args__ = (
        CheckConstraint(status_check(), name="ck_matter_status"),
        Index("ix_matter_owner", "owner_user_id"),
        Index("ix_matter_status", "status"),
        Index("ix_matter_subject", "subject_party_id"),
        Index("ix_matter_reference", "reference"),
        Index("ix_matter_deleted", "deleted_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int | None = Field(default=None)

    title: str = Field(max_length=255)
    # Free text at first - medicaid, insurance, tax, estate. A fixed list
    # would be this app guessing what cases exist before it has seen two.
    kind: str | None = Field(default=None, max_length=64)
    reference: str | None = Field(default=None, max_length=128)

    subject_party_id: int | None = Field(default=None)
    counterpart_party_id: int | None = Field(default=None)

    status: str = Field(default="open", max_length=16)
    opened_on: date | None = Field(default=None)
    closed_on: date | None = Field(default=None)
    note: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    deleted_at: datetime | None = Field(default=None)


class MatterParticipant(SQLModel, table=True):
    """A party's role in ONE matter.

    The role lives here and not on the party, which is the whole reason
    ST-01 left it off: Eleanor Nursing Care is the facility in this case
    and a payee in the ledger, and neither fact belongs to the other.
    """

    __tablename__ = "matter_participant"
    __table_args__ = (
        Index("ix_matter_participant_matter", "matter_id"),
        Index("ix_matter_participant_party", "party_id"),
        Index(
            "uq_matter_participant",
            "matter_id",
            "party_id",
            "role",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    matter_id: int = Field()
    party_id: int = Field()
    role: str = Field(max_length=32)
    note: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class DocumentParty(SQLModel, table=True):
    """Who wrote a document, and whom it concerns.

    Provenance in the sense of WHO and HOW, not only when. A letter
    filed under a matter still has to say which agency sent it and which
    person it is about, because "a letter from the county about James"
    is the thing somebody searches for.

    ``document_id`` is a plain column, never a foreign key: the
    documents service stores paper and is deliberately ignorant of what
    it means, so the reference points one way only.
    """

    __tablename__ = "document_party"
    __table_args__ = (
        Index("ix_document_party_document", "document_id"),
        Index("ix_document_party_party", "party_id"),
        Index("uq_document_party", "document_id", "party_id", "role", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field()
    party_id: int = Field()
    role: str = Field(max_length=32)
    created_at: datetime = Field(default_factory=_utcnow)


# What a request is, taken together. NOT stored - ``overdue`` is a
# reading of the due date and the clock, and a stored flag is wrong from
# the first midnight after somebody writes it.
REQUEST_STATUSES = ("open", "satisfied", "waived")
ITEM_STATUSES = ("needed", "satisfied", "not_applicable", "waived")


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

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
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

    status: str = Field(default="needed", max_length=20)
    resolution: str | None = Field(default=None)
    # The paper that answers this item. A plain column, never an FK:
    # the documents service says what a document MEANS is the consuming
    # application's business, so the reference points one way only.
    document_id: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


def matter_tag(matter_id: int) -> str:
    """The one label that files a document against a matter.

    The same shape as finance's ``account_tag`` and for the same reason:
    the documents service says outright that what a tag means differs
    per application, so steward's meaning is written once and every
    caller reads it from here.
    """
    return f"matter:{matter_id}"
