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

    @model_validator(mode="after")
    def _says_something_it_can_mean(self) -> MetadataPayload:
        if not self.fields():
            raise ValueError("Nothing to change.")
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


async def metadata_execute(
    db: AsyncSession, payload: MetadataPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.documents.service import DocumentService

    await _document(db, payload.document_id)
    await DocumentService(db).update(payload.document_id, payload.stored())
    await db.flush()
    return {"document_id": payload.document_id, "read": list(payload.fields())}


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
