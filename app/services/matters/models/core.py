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

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, Index
from sqlmodel import Field, SQLModel

from app.core.clock import utcnow
from app.core.schema import one_of

PARTY_KINDS = ("person", "organization")


def kind_check() -> str:
    """The CHECK clause for ``PARTY_KINDS``, spelled from the tuple so the
    constraint cannot drift from the values the app writes."""
    return one_of("kind", PARTY_KINDS)


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

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
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
    return one_of("status", MATTER_STATUSES)


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

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
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
    created_at: datetime = Field(default_factory=utcnow)


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
    created_at: datetime = Field(default_factory=utcnow)


# What a request is, taken together. NOT stored - ``overdue`` is a
# reading of the due date and the clock, and a stored flag is wrong from
# the first midnight after somebody writes it.
MATTER_TAG_PREFIX = "matter:"


def matter_tag(matter_id: int) -> str:
    """The one label that files a document against a matter.

    The same shape as finance's ``account_tag`` and for the same reason:
    the documents service says outright that what a tag means differs
    per application, so steward's meaning is written once and every
    caller reads it from here.
    """
    return f"{MATTER_TAG_PREFIX}{matter_id}"


def party_tag(party_id: int) -> str:
    """The one label that files a document against a contact: a place's
    statements and letters, kept with the place whatever matter later
    needs them."""
    return f"party:{party_id}"


PARTY_TAG_PREFIX = "party:"


# What a fact is a claim ABOUT. A slug, because the letter's own wording
# is a label and two letters word the same question differently - the
# slug is what a form field or a tool can be asked for by name.
FACT_ATTRIBUTES = (
    ("gross_income", "Gross income"),
    ("net_income", "Net income"),
    ("account_balance", "Account balance"),
    ("resource_value", "Resource value"),
    ("premium", "Premium withheld"),
    ("other", "Something else"),
)

# How often the money arrives. A pension portal quotes a DAILY rate and
# the county asks for a monthly figure; storing the rate as quoted and
# saying over what period keeps the app from filing arithmetic as a
# quotation.
FACT_PERIODS = ("once", "day", "week", "month", "year")

# Where the number came from, which is the whole point of the row. The
# same figure means different things depending on whether it came from a
# deposit, a statement or a benefit letter, and only one of those is fit
# to put on a government form.
FACT_PROVENANCE = ("stated", "document", "ledger")


class Fact(SQLModel, table=True):
    """A claim about someone's money at a moment, with its source.

    Append-only in spirit: a corrected figure SUPERSEDES rather than
    replaces, so the number that was filed last year is still
    recoverable when somebody asks what you told them.

    Two facts for the same subject, attribute and date are normal and
    deliberate - a deposit says one thing and a benefit letter another,
    and recording that they disagree is the feature. Deciding which is
    right is the reader's.
    """

    __tablename__ = "fact"
    __table_args__ = (
        Index("ix_fact_subject", "subject_party_id"),
        Index("ix_fact_matter", "matter_id"),
        Index("ix_fact_account", "account_id"),
        Index("ix_fact_attribute", "attribute"),
        Index("ix_fact_document", "document_id"),
        Index("ix_fact_source_party", "source_party_id"),
        Index("ix_fact_deleted", "deleted_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int | None = Field(default=None)
    subject_party_id: int = Field()
    # The case it was gathered for, if any. A fact outlives the matter:
    # what James's pension paid in August is true whether or not the
    # renewal that asked is still open.
    matter_id: int | None = Field(default=None)

    # The account this is about, where it is about one: "$1,004.93 a
    # month" belongs on the pension that pays it, not only on the man it
    # pays. A plain column, like every other reference here.
    account_id: int | None = Field(default=None)
    attribute: str = Field(max_length=40)
    # What the source calls it - "IBEW pension", "Eleanor incidental".
    # The slug says what KIND of claim it is; this says which one.
    label: str | None = Field(default=None, max_length=120)
    value_cents: int | None = Field(default=None)
    period: str = Field(default="once", max_length=10)
    # For an answer that is not money: a policy number, a vehicle, a yes.
    text_value: str | None = Field(default=None)
    as_of: date | None = Field(default=None)

    provenance: str = Field(default="stated", max_length=16)
    document_id: int | None = Field(default=None)
    # The place it was read off: an organization in the address book,
    # so "the pension portal" is a row somebody can open rather than a
    # phrase two facts spell differently.
    source_party_id: int | None = Field(default=None)
    page: int | None = Field(default=None)
    # Where a stated figure came from: a portal, a phone call, a person.
    source_note: str | None = Field(default=None)
    # The page it was read off. A pension portal is where the number
    # lives and the only way back to it is the address - "the pension
    # site" is not a way back to anything a year later.
    source_url: str | None = Field(default=None, max_length=500)
    # Checked against the source by a human. Never set by extraction.
    verified: bool = Field(default=False)
    superseded_by_id: int | None = Field(default=None)

    note: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    deleted_at: datetime | None = Field(default=None)


class SignIn(SQLModel, table=True):
    """How you get into a party's account somewhere.

    Managing somebody's affairs means logging in as them - the pension
    portal, the facility's billing page, the county's upload site - and
    a password that lives on a sticky note beside the laptop is the
    thing this replaces, not improves on.

    The secret is AES-256-GCM at rest, bound to the row, so a copy of
    the database is not a copy of the logins. The app server holds the
    key: this protects a stolen dump, not a stolen machine, and says so
    rather than implying more.
    """

    __tablename__ = "sign_in"
    __table_args__ = (
        Index("ix_sign_in_party", "party_id"),
        Index("ix_sign_in_site", "site_party_id"),
        Index("ix_sign_in_deleted", "deleted_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int | None = Field(default=None)
    party_id: int = Field()
    # Whose account it is, and WHERE. Two different parties: James's
    # login at the IBEW pension fund is one row naming both.
    site_party_id: int | None = Field(default=None)
    label: str = Field(max_length=120)
    url: str | None = Field(default=None, max_length=500)
    username: str | None = Field(default=None, max_length=255)
    # Never the password itself. The column name says what is in it so
    # nobody writes plaintext here by accident.
    secret_encrypted: str | None = Field(default=None)
    note: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    deleted_at: datetime | None = Field(default=None)


# What a matter does that leaves no paper. Four kinds and not a
# workflow: a call, something posted, somebody visited, and a note for
# the rest. A fixed list because these are TYPED by a person in a hurry
# and a free-text kind is a column that reads "phone", "Phone call",
# "called" and sorts as three things.
MATTER_EVENT_KINDS = ("call", "mailed", "visit", "note")


class MatterEvent(SQLModel, table=True):
    """Something that happened on a case and left nothing behind.

    The rest of a matter's story is already dated rows - a request came
    in, a figure is as of a date, a paper answered an ask - and the
    timeline is derived from those so it can never disagree with them.
    This table is only the part nothing else records: "called DSS,
    confirmed receipt", "mailed the POA". Three years on, that sequence
    is the difference between a case and a pile of paper (ST-10).

    Documents do NOT attach to events and events do not own documents.
    Both attach to the matter; the timeline is the join, not a
    container. ``document_id`` and ``party_id`` are here to say what a
    call was ABOUT and who it was WITH, which is a different claim.

    Deleted for real rather than marked deleted: nothing points at an
    event, and a line somebody typed by mistake should leave.
    """

    __tablename__ = "matter_event"
    __table_args__ = (
        CheckConstraint(one_of("kind", MATTER_EVENT_KINDS), name="ck_matter_event_kind"),
        Index("ix_matter_event_matter", "matter_id"),
        Index("ix_matter_event_occurred", "occurred_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int | None = Field(default=None)
    matter_id: int = Field()

    # The day it happened, not the day it was typed: somebody enters
    # Tuesday's call on Friday, and Friday is not where it belongs.
    occurred_at: date = Field()
    kind: str = Field(default="note", max_length=16)
    summary: str = Field()

    document_id: int | None = Field(default=None)
    party_id: int | None = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)
