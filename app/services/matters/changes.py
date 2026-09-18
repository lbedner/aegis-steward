"""Matter writes as proposals: what Illiana can put in front of you.

Working a matter adds CHANGE TYPES, not tools. The one write tool is
``propose``, and it is change-type generic - so a fact she reads off a
letter arrives as a card you approve, the same way a category or a
valuation does, and nothing she says is true until you say so.

Registered into the finance write registry because that is where the
propose/approve machinery lives. This is its second consumer and the
first from outside finance, which is the count ST-08 said to wait for
before deciding whether that machinery moves somewhere shared. The seam
is visible here on purpose rather than papered over with a copy.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.schema import known
from app.services.finance.schemas import ChangeDisplayRow
from app.services.matters.facts import ATTRIBUTE_KEYS, LABELS, monthly_cents
from app.services.matters.models import (
    FACT_PERIODS,
    FACT_PROVENANCE,
    ITEM_KINDS,
)


class RecordFactPayload(BaseModel):
    """A claim about somebody's money, with where it came from.

    The provenance is required rather than defaulted, because the whole
    point of the row is that a deposit, a statement and a benefit letter
    are three different claims and only one of them answers a form.
    """

    model_config = ConfigDict(extra="forbid")

    subject_party_id: int
    attribute: str
    provenance: str
    label: str | None = None
    value_cents: int | None = Field(default=None, ge=0)
    period: str = "once"
    text_value: str | None = None
    as_of: date | None = None
    matter_id: int | None = None
    account_id: int | None = None
    document_id: int | None = None
    page: int | None = None
    source_party_id: int | None = None
    source_url: str | None = None
    source_note: str | None = None
    note: str | None = None

    _known_attribute = field_validator("attribute")(known(ATTRIBUTE_KEYS))

    _known_period = field_validator("period")(known(FACT_PERIODS))

    @model_validator(mode="after")
    def _a_document_fact_says_where(self) -> RecordFactPayload:
        """"provenance: document" is not provenance. It is a claim that
        provenance exists somewhere.

        The service already refused a document-derived fact with no
        document. It did not insist on the PAGE or the line, so a
        statement could be cited whole: nine pages, one of which says
        the number, and no way to know which without opening it. A
        figure without its provenance is not fit to put on a government
        form, and this is the field that decides whether it has any.

        Only for ``document``. Somebody saying a number out loud has no
        page, and the rule is about what this provenance CLAIMS.
        """
        if self.provenance != "document":
            return self
        if self.document_id is None:
            raise ValueError("A document-derived fact needs the document.")
        if self.page is None:
            raise ValueError(
                "A document-derived fact needs the page it was read from."
            )
        if not (self.source_note or "").strip():
            raise ValueError(
                "A document-derived fact needs the line it was read from: a "
                "number nobody can check against the page is a number nobody "
                "should put on a form."
            )
        return self

    _known_provenance = field_validator("provenance")(known(FACT_PROVENANCE))


async def record_fact_execute(
    db: AsyncSession, payload: RecordFactPayload, owner_user_id: int | None
) -> dict[str, Any]:
    """File the fact. Never verified: the flag means a person checked it
    against the source, and approving a card is approving what was
    READ, not confirming the source was opened."""
    from app.services.matters.facts import FactService

    fact = await FactService(db).record(
        subject_party_id=payload.subject_party_id,
        matter_id=payload.matter_id,
        account_id=payload.account_id,
        attribute=payload.attribute,
        label=payload.label,
        value_cents=payload.value_cents,
        period=payload.period,
        text_value=payload.text_value,
        as_of=payload.as_of,
        provenance=payload.provenance,
        document_id=payload.document_id,
        page=payload.page,
        source_party_id=payload.source_party_id,
        source_url=payload.source_url,
        source_note=payload.source_note,
        owner_user_id=owner_user_id,
        note=payload.note,
    )
    await db.flush()
    return {"fact_id": fact.id, "attribute": fact.attribute}


async def record_fact_describe(
    db: AsyncSession, payload: RecordFactPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    """The card: whose, what, how much, and how it is known - because
    the source is what makes the number usable, and a card that hides it
    is asking for approval of half the claim."""
    from app.services.finance.domains.detection.insights.formatting import format_usd
    from app.services.matters.service import PartyService

    parties = PartyService(db)
    subject = await parties.get(payload.subject_party_id)
    rows = [
        ChangeDisplayRow(label="About", value=subject.name if subject else "Unknown"),
        ChangeDisplayRow(
            label=LABELS.get(payload.attribute, payload.attribute),
            value=payload.label or "-",
        ),
    ]
    if payload.value_cents is not None:
        said = format_usd(payload.value_cents)
        if payload.period != "once":
            said = f"{said} a {payload.period}"
        monthly = monthly_cents(payload.value_cents, payload.period)
        if monthly is not None and payload.period not in ("once", "month"):
            said = f"{said} (about {format_usd(monthly)} a month)"
        rows.append(ChangeDisplayRow(label="Figure", value=said))
    if payload.text_value:
        rows.append(ChangeDisplayRow(label="Says", value=payload.text_value))
    if payload.as_of:
        rows.append(ChangeDisplayRow(label="As of", value=payload.as_of.isoformat()))
    source = payload.provenance
    if payload.source_party_id:
        place = await parties.get(payload.source_party_id)
        if place is not None:
            source = f"{source} · {place.name}"
    if payload.source_note:
        source = f"{source} · {payload.source_note}"
    rows.append(ChangeDisplayRow(label="How it is known", value=source))
    if already := await _already_said(db, payload):
        rows.append(ChangeDisplayRow(label="Already on file", value=already))
    return rows


async def _already_said(db: AsyncSession, payload: RecordFactPayload) -> str | None:
    """The same figure, already recorded about the same person.

    Live: the NYSLRS gross benefit went in on 16 September and again on
    18 September - same subject, same attribute, same value, same
    document, same page - and nothing said a word. A ledger that answers
    "what is his gross income" with two identical rows has made the
    question harder than it was on paper.

    NOT refused. A figure really can be recorded twice, from two
    statements or restated after a change, and only the person
    approving knows which this is. So it is SAID, the way
    ``account.create`` says "You already have" - the expensive mistake
    is the one nobody was shown.
    """
    if payload.value_cents is None:
        return None
    from app.services.finance.domains.detection.insights.formatting import format_usd
    from app.services.matters.facts import FactService

    same = [
        fact
        for fact in await FactService(db).find(
            subject_party_id=payload.subject_party_id,
            attribute=payload.attribute,
        )
        if fact.value_cents == payload.value_cents and fact.period == payload.period
    ]
    if not same:
        return None
    said = format_usd(payload.value_cents)
    if payload.period != "once":
        said = f"{said} a {payload.period}"
    when = same[0].as_of or same[0].created_at.date()
    more = f" (and {len(same) - 1} more)" if len(same) > 1 else ""
    return f"{said}, recorded {when}{more}"


# --- Asks: what a letter obliges you to produce -------------------------
#
# Illiana reads the letter (``paper``) and the asks (``requests``) and can
# see when they disagree - "the letter says July 1 and NYSLRS; the ask
# says August 1 and IBEW". Without a change type she can only say so.
# These let her propose the correction, and the person approves it the
# way every other write is approved.

ITEM_KIND_KEYS = tuple(key for key, _label in ITEM_KINDS)


def known_item_kind(value: str | None) -> str | None:
    """The one rule for what an ask's ``kind`` may be, for every payload
    that carries one."""
    if value is not None and value not in ITEM_KIND_KEYS:
        raise ValueError(f"kind must be one of {', '.join(ITEM_KIND_KEYS)}")
    return value


class AmendAskPayload(BaseModel):
    """Correct an ask: its wording, the kind of work, or the date it is
    asked as of. Only what is given changes; the rest stands."""

    model_config = ConfigDict(extra="forbid")

    item_id: int
    asked: str | None = None
    kind: str | None = None
    as_of: date | None = None
    reason: str | None = None

    _kind_is_known = field_validator("kind")(known_item_kind)


class AddAskPayload(BaseModel):
    """A new ask on a request - one the letter makes and the record
    does not yet."""

    model_config = ConfigDict(extra="forbid")

    request_id: int
    asked: str
    kind: str = "document"
    as_of: date | None = None
    reason: str | None = None

    _kind_is_known = field_validator("kind")(known_item_kind)


async def amend_ask_execute(
    db: AsyncSession, payload: AmendAskPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.matters.requests import RequestService

    item = await RequestService(db).amend(
        payload.item_id, asked=payload.asked, kind=payload.kind, as_of=payload.as_of
    )
    if item is None:
        raise ValueError(f"No ask with id {payload.item_id}")
    await db.flush()
    return {"item_id": item.id, "asked": item.asked}


async def amend_ask_describe(
    db: AsyncSession, payload: AmendAskPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    """The card: what it says now, what it would say, and why."""
    from app.services.matters.requests import RequestService
    from app.services.matters.words import item_kind

    item = await RequestService(db).item(payload.item_id)
    rows = [ChangeDisplayRow(label="Ask", value=item.asked if item else "Unknown")]
    if payload.asked is not None:
        rows.append(ChangeDisplayRow(label="Would read", value=payload.asked))
    if payload.kind is not None:
        rows.append(ChangeDisplayRow(label="Kind", value=item_kind(payload.kind)))
    if payload.as_of is not None:
        rows.append(ChangeDisplayRow(label="As of", value=payload.as_of.isoformat()))
    if payload.reason:
        rows.append(ChangeDisplayRow(label="Because", value=payload.reason))
    return rows


async def add_ask_execute(
    db: AsyncSession, payload: AddAskPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.matters.requests import RequestService

    item = await RequestService(db).add_item(
        payload.request_id, asked=payload.asked, kind=payload.kind, as_of=payload.as_of
    )
    await db.flush()
    return {"item_id": item.id, "asked": item.asked}


async def add_ask_describe(
    db: AsyncSession, payload: AddAskPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.matters.requests import RequestService
    from app.services.matters.words import item_kind

    request = await RequestService(db).get(payload.request_id)
    rows = [
        ChangeDisplayRow(
            label="On the request",
            value=f"due {request.due_on.isoformat()}"
            if request and request.due_on
            else "-",
        ),
        ChangeDisplayRow(label="Ask", value=payload.asked),
        ChangeDisplayRow(label="Kind", value=item_kind(payload.kind)),
    ]
    if payload.as_of is not None:
        rows.append(ChangeDisplayRow(label="As of", value=payload.as_of.isoformat()))
    if payload.reason:
        rows.append(ChangeDisplayRow(label="Because", value=payload.reason))
    return rows


class AttachAskPayload(BaseModel):
    """The paper on the shelf that answers an ask. Attaching IS the
    answer, as it is from the matter page; the document is filed on the
    matter as well, so the case's paper stays findable."""

    model_config = ConfigDict(extra="forbid")

    item_id: int
    document_id: int
    reason: str | None = None


async def attach_ask_execute(
    db: AsyncSession, payload: AttachAskPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.documents.service import DocumentService
    from app.services.matters.models import matter_tag
    from app.services.matters.requests import RequestService

    requests = RequestService(db)
    item = await requests.item(payload.item_id)
    if item is None:
        raise ValueError(f"No ask with id {payload.item_id}")
    request = await requests.get(item.request_id)
    documents = DocumentService(db)
    document = await documents.get(payload.document_id)
    if document is None or request is None:
        raise ValueError(f"No document with id {payload.document_id}")
    await documents.tag(document.id, matter_tag(request.matter_id))
    await requests.attach(item.id, document.id, document.title)
    await db.flush()
    return {"item_id": item.id, "document_id": document.id}


async def attach_ask_describe(
    db: AsyncSession, payload: AttachAskPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.documents.service import DocumentService
    from app.services.matters.requests import RequestService

    item = await RequestService(db).item(payload.item_id)
    document = await DocumentService(db).get(payload.document_id)
    rows = [
        ChangeDisplayRow(label="Ask", value=item.asked if item else "-"),
        ChangeDisplayRow(label="Document", value=document.title if document else "-"),
    ]
    if payload.reason:
        rows.append(ChangeDisplayRow(label="Because", value=payload.reason))
    return rows
