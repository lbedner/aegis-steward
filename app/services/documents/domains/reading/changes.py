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

    def stored(self, fields: dict[str, ReadValue] | None = None) -> dict[str, Any]:
        """What the document service is asked to write."""
        return {
            name: date.fromisoformat(read.value)
            if name == "document_date"
            else read.value
            for name, read in (self.fields() if fields is None else fields).items()
        }


def still_applies(document: Any, payload: MetadataPayload) -> dict[str, ReadValue]:
    """The proposed fields that would still change something.

    A card sits in the queue for as long as it takes somebody to get to
    it, and the document is not frozen meanwhile. Two ways a reading
    goes stale, and a person is right in both:

    A field somebody has since set to the same value is nothing to
    decide. And a TITLE proposal exists BECAUSE the document was still
    named after a file - if somebody has given it a name of their own,
    the premise is gone and their name stands. Applying it anyway would
    overwrite a person's word with a machine's, silently, which is what
    this did before anybody asked (2026-09-19).
    """
    from app.services.documents.domains.reading.titles import looks_like_a_filename

    still = {}
    for name, read in payload.fields().items():
        standing = getattr(document, name, None)
        if str(standing or "") == str(read.value):
            continue
        if name == "title" and not looks_like_a_filename(str(standing or "")):
            continue
        still[name] = read
    return still


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

    document = await _document(db, payload.document_id)
    party = await _sender(db, payload)
    service = DocumentService(db)
    # What it would STILL change. A card is decided later than it was
    # made, and a person who named the document in between has said
    # something this must not undo.
    if fields := still_applies(document, payload):
        await service.update(payload.document_id, payload.stored(fields))
    if party is not None:
        # ``tag`` is idempotent, so re-reading a document files it once.
        await service.tag(payload.document_id, party_tag(party.id))
    await db.flush()
    return {
        "document_id": payload.document_id,
        "read": list(fields),
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
    # Only what it would still do. A reading that has been overtaken -
    # by the same value arriving another way, or by somebody naming the
    # document themselves - is not a decision anybody should be asked
    # to make.
    still = still_applies(document, payload)
    rows.extend(
        ChangeDisplayRow(
            label=labels[name],
            value=f"{getattr(document, name) or '-'} → {read.value} · {read.cited()}",
            document_id=payload.document_id,
            page=read.page,
        )
        for name, read in still.items()
    )
    if not still and payload.sender is None:
        raise ValueError(
            f"This would change nothing about {document.title}: it reads that "
            "way already, or somebody has named it since."
        )
    # The sender by NAME. An id on a card is a number somebody has to go
    # and look up before they can say yes.
    if (party := await _sender(db, payload)) is not None:
        rows.append(
            ChangeDisplayRow(
                label="From",
                value=f"{party.name} · {payload.sender.cited()}",
                document_id=payload.document_id,
                page=payload.sender.page,
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
    # Each demand cites its own page, and the card draws the FIRST of
    # them: a letter's demands are usually on one page, and a card that
    # draws five pages is a card nobody scrolls to the verbs of.
    rows.extend(
        ChangeDisplayRow(
            label=item_kind(ask.kind),
            value=f"{ask.asked} · {ask.cited()}",
            document_id=payload.document_id,
            page=ask.page,
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
        # The page itself, beside the line it is quoted from.
        ChangeDisplayRow(
            label="Because",
            value=payload.cited(),
            document_id=payload.document_id,
            page=payload.page,
        ),
    ]
