"""Document writes as proposals: ``document.metadata``.

What a document says it IS goes through the approval queue like
everything else. A regex or a model silently mis-reading the execution
date on a legal document is exactly the failure the queue exists to
prevent, and a metadata field is no less load-bearing than a figure.

Every field carries the page and the line it was read from, because a
claim nobody can check is a claim nobody should approve.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.schema import require_one_of
from app.services.documents.models import DOCUMENT_KINDS
from app.services.finance.schemas import ChangeDisplayRow

# The fields a reading may propose, and how the card labels them.
FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("kind", "Kind"),
    ("document_date", "Dated"),
)


class ReadValue(BaseModel):
    """A value and where it was read. The citation is not optional: a
    field that cannot name its page is not a reading, it is a guess."""

    model_config = ConfigDict(extra="forbid")

    value: str
    page: int
    because: str

    def cited(self) -> str:
        return f"page {self.page}: {self.because}"


class MetadataPayload(BaseModel):
    """What a document says about itself. Only what is given changes;
    the bytes never do, because a document is what arrived."""

    model_config = ConfigDict(extra="forbid")

    document_id: int
    title: ReadValue | None = None
    kind: ReadValue | None = None
    document_date: ReadValue | None = None
    # WHO SENT IT, as a ``parties()`` id in ``value``. Not a column on
    # the document but a tag, because a document can involve several
    # people - a letter FROM the county ABOUT a resident - and the
    # sender is one of those rather than a different kind of fact.
    #
    # It is also what makes the shelf readable by letterhead: until a
    # document is attached to its sender, the address printed at the top
    # of it belongs to nobody. Four parties were tagged to one statement
    # and the letter carrying the county's own address was attached to
    # nothing (2026-09-18).
    sender: ReadValue | None = None

    @model_validator(mode="after")
    def _says_something_it_can_mean(self) -> MetadataPayload:
        if not self.fields() and self.sender is None:
            raise ValueError("Nothing to change.")
        if self.sender is not None and not self.sender.value.strip().isdigit():
            raise ValueError(
                "The sender is a parties() id, not a name: a name typed here "
                "is a second directory nobody can join to the first."
            )
        if self.kind is not None:
            require_one_of(self.kind.value, DOCUMENT_KINDS)
        if self.document_date is not None:
            date.fromisoformat(self.document_date.value)
        return self

    def fields(self) -> dict[str, ReadValue]:
        """The proposed fields, in the order the card reads them."""
        return {
            name: read
            for name, _label in FIELDS
            if (read := getattr(self, name)) is not None
        }

    def stored(self) -> dict[str, Any]:
        """What the document service is asked to write."""
        return {
            name: date.fromisoformat(read.value)
            if name == "document_date"
            else read.value
            for name, read in self.fields().items()
        }


async def _document(db: AsyncSession, document_id: int) -> Any:
    from app.services.documents.service import DocumentService

    found = await DocumentService(db).get(document_id)
    if found is None:
        raise ValueError(f"No document with id {document_id}")
    return found


async def _sender(db: AsyncSession, payload: MetadataPayload) -> Any:
    """The party a reading says sent this, or None."""
    if payload.sender is None:
        return None
    from app.services.matters.service import PartyService

    party = await PartyService(db).get(int(payload.sender.value))
    if party is None:
        raise ValueError(f"No contact with id {payload.sender.value}")
    return party


async def metadata_execute(
    db: AsyncSession, payload: MetadataPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.documents.service import DocumentService
    from app.services.matters.models import party_tag

    await _document(db, payload.document_id)
    party = await _sender(db, payload)
    service = DocumentService(db)
    if payload.fields():
        await service.update(payload.document_id, payload.stored())
    if party is not None:
        # ``tag`` is idempotent, so re-reading a document files it once.
        await service.tag(payload.document_id, party_tag(party.id))
    await db.flush()
    return {
        "document_id": payload.document_id,
        "read": list(payload.fields()),
        "sender_party_id": party.id if party else None,
    }


async def metadata_describe(
    db: AsyncSession, payload: MetadataPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    """Before, after, and where it was read - the three things somebody
    needs to say yes."""
    document = await _document(db, payload.document_id)
    labels = dict(FIELDS)
    rows = [ChangeDisplayRow(label="Document", value=document.title)]
    rows.extend(
        ChangeDisplayRow(
            label=labels[name],
            value=f"{getattr(document, name) or '-'} → {read.value} · {read.cited()}",
        )
        for name, read in payload.fields().items()
    )
    # The sender by NAME. An id on a card is a number somebody has to go
    # and look up before they can say yes.
    if (party := await _sender(db, payload)) is not None:
        rows.append(
            ChangeDisplayRow(
                label="From", value=f"{party.name} · {payload.sender.cited()}"
            )
        )
    return rows


class ReadAsk(BaseModel):
    """One demand a letter made, and where it was read."""

    model_config = ConfigDict(extra="forbid")

    asked: str
    kind: str = "document"
    page: int
    quote: str

    def cited(self) -> str:
        return f"page {self.page}: {self.quote}"


class RequestPayload(BaseModel):
    """A letter's demands as one card: the matter it belongs to, the
    dates it states, and every ask it makes.

    One card, not one per ask, because a letter is answered as a whole:
    approving half a demand list leaves a matter that looks handled.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: int
    matter_id: int
    received_on: date | None = None
    due_on: date | None = None
    items: list[ReadAsk] = Field(min_length=1)
    # Demands the reading found and could not stand behind. Carried so
    # the card can say it is incomplete: a list somebody trusts as whole
    # is worse than a list that admits its gap.
    dropped: int = Field(default=0, ge=0)


async def _matter(db: AsyncSession, matter_id: int) -> Any:
    from app.services.matters.matters import MatterService

    found = await MatterService(db).get(matter_id)
    if found is None:
        raise ValueError(f"No matter with id {matter_id}")
    return found


async def request_execute(
    db: AsyncSession, payload: RequestPayload, owner_user_id: int | None
) -> dict[str, Any]:
    """File the letter's demands on the matter.

    The page and the quote stay on the CARD rather than on the item: the
    request cites the document it came from, and the frozen display in
    the queue keeps what each line was read off, which is the audit.
    """
    from app.services.matters.requests import RequestService

    await _matter(db, payload.matter_id)
    await _document(db, payload.document_id)
    request = await RequestService(db).record(
        matter_id=payload.matter_id,
        document_id=payload.document_id,
        received_on=payload.received_on,
        due_on=payload.due_on,
        owner_user_id=owner_user_id,
        items=[{"asked": ask.asked, "kind": ask.kind} for ask in payload.items],
    )
    await db.flush()
    return {"request_id": request.id, "items": len(payload.items)}


async def request_describe(
    db: AsyncSession, payload: RequestPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.matters.words import item_kind

    matter = await _matter(db, payload.matter_id)
    document = await _document(db, payload.document_id)
    rows = [
        ChangeDisplayRow(label="Matter", value=matter.title),
        ChangeDisplayRow(label="Read from", value=document.title),
    ]
    for label, when in (("Received", payload.received_on), ("Due", payload.due_on)):
        if when:
            rows.append(ChangeDisplayRow(label=label, value=when.isoformat()))
    rows.extend(
        ChangeDisplayRow(
            label=item_kind(ask.kind), value=f"{ask.asked} · {ask.cited()}"
        )
        for ask in payload.items
    )
    if payload.dropped:
        rows.append(
            ChangeDisplayRow(
                label="Not read",
                value=f"{payload.dropped} more demand"
                f"{'s' if payload.dropped > 1 else ''} could not be quoted from "
                "the page named. Read the letter beside this.",
            )
        )
    return rows


class EvidencePayload(BaseModel):
    """Which paper answers which ask, proposed rather than decided.

    ST-07 says the link is a HUMAN action, and that proposing one is
    ST-08's job. So extraction offers and the queue approves: nothing is
    linked because a model was confident about it.

    ``because`` is the line read off the page, and it is not optional.
    An approver looking at "this statement answers proof of gross
    income" has no way to judge it; one looking at "page 2: Net Benefit:
    $1004.93" has the whole question in front of them.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: int
    request_item_id: int
    page: int = Field(ge=1)
    because: str

    @model_validator(mode="after")
    def _cites_something(self) -> EvidencePayload:
        if not self.because.strip():
            raise ValueError(
                "An evidence link needs the line it was read from: a link "
                "nobody can check is a link nobody should approve."
            )
        return self

    def cited(self) -> str:
        return f"page {self.page}: {self.because.strip()}"


async def _ask(db: AsyncSession, request_item_id: int) -> Any:
    from app.services.matters.models import RequestItem

    item = await db.get(RequestItem, request_item_id)
    if item is None:
        raise ValueError(f"No ask with id {request_item_id}")
    return item


async def evidence_execute(
    db: AsyncSession, payload: EvidencePayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.matters.evidence import link

    await _document(db, payload.document_id)
    await _ask(db, payload.request_item_id)
    row = await link(
        db,
        payload.request_item_id,
        document_id=payload.document_id,
        page=payload.page,
        note=payload.because.strip(),
    )
    await db.flush()
    return {
        "evidence_link_id": row.id,
        "request_item_id": payload.request_item_id,
        "document_id": payload.document_id,
    }


async def evidence_describe(
    db: AsyncSession, payload: EvidencePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    document = await _document(db, payload.document_id)
    item = await _ask(db, payload.request_item_id)
    return [
        ChangeDisplayRow(label="Document", value=document.title),
        ChangeDisplayRow(label="Answers", value=item.asked),
        ChangeDisplayRow(label="Because", value=payload.cited()),
    ]


# How often the money arrives, said the way a card should read it.
# "$1,004.93 a month" is a sentence; "$1,004.93 month" is a database row
# somebody has to translate.
PERIOD_WORDS = {
    "once": "",
    "day": " a day",
    "week": " a week",
    "month": " a month",
    "year": " a year",
}


class FactPayload(BaseModel):
    """A figure read off a page, with where it was read.

    ST-08's validation gate: a statement produces balance facts whose
    provenance names the document and page. A figure without its
    provenance is not fit to put on a government form, which is why
    neither the page nor the line behind it is optional.

    The rate is stored AS QUOTED. A pension portal quotes a daily rate
    and the county asks for a monthly figure; converting on the way in
    files arithmetic as a quotation, and the number stops matching the
    paper it came from.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: int
    subject_party_id: int
    attribute: str
    value_cents: int = Field(ge=0)
    period: str = "once"
    page: int = Field(ge=1)
    because: str
    label: str | None = None
    as_of: date | None = None
    matter_id: int | None = None

    @model_validator(mode="after")
    def _known_and_cited(self) -> FactPayload:
        from app.services.matters.facts import ATTRIBUTE_KEYS
        from app.services.matters.models import FACT_PERIODS

        require_one_of(self.attribute, ATTRIBUTE_KEYS)
        require_one_of(self.period, FACT_PERIODS)
        if not self.because.strip():
            raise ValueError(
                "A figure needs the line it was read from: a number nobody "
                "can check against the page is a number nobody should put "
                "on a form."
            )
        return self

    def cited(self) -> str:
        return f"page {self.page}: {self.because.strip()}"


async def _subject(db: AsyncSession, party_id: int) -> Any:
    from app.services.matters.service import PartyService

    party = await PartyService(db).get(party_id)
    if party is None:
        raise ValueError(f"No contact with id {party_id}")
    return party


async def fact_execute(
    db: AsyncSession, payload: FactPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.matters.facts import FactService

    await _document(db, payload.document_id)
    await _subject(db, payload.subject_party_id)
    fact = await FactService(db).record(
        subject_party_id=payload.subject_party_id,
        attribute=payload.attribute,
        value_cents=payload.value_cents,
        period=payload.period,
        label=payload.label,
        as_of=payload.as_of,
        matter_id=payload.matter_id,
        # Never "verified": approving a card is approving what was READ,
        # not confirming somebody opened the source and checked it.
        provenance="document",
        document_id=payload.document_id,
        page=payload.page,
        source_note=payload.because.strip(),
        owner_user_id=owner_user_id,
    )
    await db.flush()
    return {"fact_id": fact.id, "subject_party_id": payload.subject_party_id}


async def fact_describe(
    db: AsyncSession, payload: FactPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.detection.insights.formatting import format_usd
    from app.services.matters.facts import LABELS

    document = await _document(db, payload.document_id)
    party = await _subject(db, payload.subject_party_id)
    money = format_usd(payload.value_cents) + PERIOD_WORDS.get(payload.period, "")
    rows = [
        ChangeDisplayRow(label="About", value=party.name),
        ChangeDisplayRow(
            label=LABELS.get(payload.attribute, payload.attribute), value=money
        ),
        ChangeDisplayRow(label="Read from", value=document.title),
        ChangeDisplayRow(label="Because", value=payload.cited()),
    ]
    if payload.as_of:
        rows.insert(
            2, ChangeDisplayRow(label="As of", value=payload.as_of.isoformat())
        )
    return rows
