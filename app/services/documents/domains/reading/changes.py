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

from pydantic import BaseModel, ConfigDict, model_validator
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
