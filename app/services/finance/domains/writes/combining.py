"""Photos that are pages of one form become one document: ``document.combine``.

Eight photos of 2025 tax forms became eight documents, and Illiana
counted eight; they were four forms - a 1099-INT's front and back, a
1095-C over four photos (#439). The card takes the photos in page order,
shelf documents or photos still in the chat, and the name the document
gets. Approving stitches them into one PDF, a page a photo; each page
keeps the text already read off its photo and the photo itself as its
picture, so nothing is read again; the document is filed everywhere the
photos were; and the photos are retired, their bytes kept.
"""

from __future__ import annotations

import io
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.writes.filing import (
    chat_entry,
    figure_rows,
    photo_key,
    place,
)
from app.services.finance.schemas import ChangeDisplayRow


class CombinePayload(BaseModel):
    """The photos, in page order, and what the document they make IS."""

    model_config = ConfigDict(extra="forbid")

    # Photos on the shelf (filed already), or photos in the chat by the
    # #id their marker shows - one or the other.
    document_ids: list[int] = []
    paste_ids: list[str] = []
    title: str
    kind: str | None = None
    form_type: str | None = None
    tax_year: int | None = None
    # What it SAYS, as printed (#442); none given keeps the photos' own.
    figures: dict[str, str] = {}
    # For chat photos: where the document is filed. Shelf photos carry
    # their own places over.
    account_id: int | None = None
    party_id: int | None = None
    matter_id: int | None = None
    transaction_id: int | None = None

    @model_validator(mode="after")
    def _pages_and_a_name(self) -> CombinePayload:
        from app.services.documents.service import check_fields, check_figures

        pages: list[Any] = self.document_ids or self.paste_ids
        if self.document_ids and self.paste_ids:
            raise ValueError("Pages are either document_ids or paste_ids, not both.")
        if len(pages) < 2:
            raise ValueError("A combined document takes two photos or more.")
        if len(set(pages)) != len(pages):
            raise ValueError("Each photo is a page once.")
        if len([p for p in self.places().values() if p is not None]) > 1:
            raise ValueError(
                "At most one of account_id, party_id, matter_id, transaction_id."
            )
        if self.form_type and not self.kind:
            self.kind = "tax"  # a form type is tax paper
        check_fields(self.naming())
        check_figures(self.figures)
        return self

    def naming(self) -> dict[str, Any]:
        """What the document is called and is, as given."""
        given = {
            "title": self.title,
            "kind": self.kind,
            "form_type": self.form_type,
            "tax_year": self.tax_year,
        }
        return {k: v for k, v in given.items() if v is not None}

    def places(self) -> dict[str, int | None]:
        return {
            "account_id": self.account_id,
            "party_id": self.party_id,
            "matter_id": self.matter_id,
            "transaction_id": self.transaction_id,
        }


async def _pages(
    db: AsyncSession, payload: CombinePayload, owner_user_id: int | None
) -> list[dict[str, Any]]:
    """Each page as {title, key, document_id}, in order: one read for
    shelf photos, the chat index for the rest. A page that is not a photo
    is refused by name."""
    from app.services.documents.queries import documents_by_ids

    if payload.document_ids:
        found = await documents_by_ids(db, payload.document_ids)
        pages = []
        for document_id in payload.document_ids:
            document = found.get(document_id)
            if document is None or not (document.media_type or "").startswith("image/"):
                raise ValueError(f"Document {document_id} is not a photo on the shelf.")
            pages.append(
                {
                    "title": document.title,
                    "key": document.storage_key,
                    "document_id": document_id,
                    "figures": (document.meta_data or {}).get("figures") or {},
                }
            )
        return pages
    pages = []
    for paste_id in payload.paste_ids:
        entry = await chat_entry(db, paste_id, owner_user_id)
        if (key := photo_key(entry)) is None:
            raise ValueError(f"{paste_id!r} is not a photo attached in the chat.")
        pages.append(
            {
                "title": str((entry or {}).get("title") or "photo"),
                "key": key,
                "document_id": None,
            }
        )
    return pages


def _pdf(images: list[bytes]) -> bytes:
    """One PDF, a page a photo, in order."""
    from PIL import Image

    opened = [Image.open(io.BytesIO(data)).convert("RGB") for data in images]
    out = io.BytesIO()
    opened[0].save(out, "PDF", save_all=True, append_images=opened[1:], resolution=150)
    return out.getvalue()


async def combine_execute(
    db: AsyncSession, payload: CombinePayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.core.storage import get_storage
    from app.services.documents.models import DocumentPage
    from app.services.documents.queries import first_pages, tags_for_many
    from app.services.documents.service import DocumentService

    pages = await _pages(db, payload, owner_user_id)
    store = get_storage()
    images = [await store.get(page["key"]) for page in pages]
    if any(data is None for data in images):
        raise ValueError("A photo is no longer in storage; attach it again.")
    documents = DocumentService(db)
    made = await documents.ingest(
        _pdf([data for data in images if data is not None]),
        title=payload.title,
        media_type="application/pdf",
        owner_user_id=owner_user_id,
        source="combined",
        page_count=len(pages),
    )
    made_id = int(made.id or 0)
    await documents.update(made_id, payload.naming())
    kept: dict[str, str] = {}
    for page in pages:
        kept.update(page.get("figures") or {})
    await documents.add_figures(made_id, payload.figures or kept)
    labels: set[str] = set()
    if any(v is not None for v in payload.places().values()):
        labels.add((await place(db, owner_user_id, **payload.places()))[0])
    originals = [int(page["document_id"]) for page in pages if page["document_id"]]
    if originals:
        for found in (await tags_for_many(db, originals)).values():
            labels.update(found)
        # Each page as it was read off its photo: no second reading.
        read = await first_pages(db, originals)
        for number, page in enumerate(pages, start=1):
            was = read.get(int(page["document_id"]))
            db.add(
                DocumentPage(
                    document_id=made_id,
                    page_number=number,
                    status=was.status if was else "unread",
                    method=was.method if was else "none",
                    text=was.text if was else None,
                    model=was.model if was else None,
                    image_key=page["key"],
                )
            )
        for original in originals:
            await documents.soft_delete(original, owner_user_id=owner_user_id)
    for label in sorted(labels):
        await documents.tag(made_id, label)
    await db.flush()
    # Chat photos were never read: the reading runs once the approval commits.
    return {"document_id": made_id, "retired": originals, "read": not originals}


async def combine_describe(
    db: AsyncSession, payload: CombinePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    pages = await _pages(db, payload, owner_user_id)
    rows = [ChangeDisplayRow(label="Document", value=payload.title)]
    rows += [
        ChangeDisplayRow(label=f"Page {n}", value=page["title"], scan=True)
        for n, page in enumerate(pages, start=1)
    ]
    if what := [str(v) for k, v in payload.naming().items() if k != "title"]:
        rows.append(ChangeDisplayRow(label="Is", value=" · ".join(what)))
    rows += figure_rows(payload.figures)
    if any(v is not None for v in payload.places().values()):
        _tag, name = await place(db, owner_user_id, **payload.places())
        rows.append(ChangeDisplayRow(label="File under", value=name))
    elif payload.document_ids:
        rows.append(ChangeDisplayRow(label="File under", value="where its photos are"))
    return rows


async def combine_scan(
    db: AsyncSession, payload: CombinePayload, owner_user_id: int | None
) -> list[str]:
    """Each photo, as its page."""
    return [page["key"] for page in await _pages(db, payload, owner_user_id)]
