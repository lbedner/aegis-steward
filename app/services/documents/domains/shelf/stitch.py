"""Pictures that are pages of one paper, made one document.

The one place a document is made from pictures: photos combined into a
form (``document.combine``, #439) and a check's front and back (#582),
which were filed as two documents a check and read as two things. One
PDF, a page a picture, and each page keeps its picture as its image and
the text already read off it, so nothing is read twice.
"""

from __future__ import annotations

import io
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.models import Document, DocumentPage


def _pdf(images: list[bytes]) -> bytes:
    """One PDF, a page a picture, in order."""
    from PIL import Image

    opened = [Image.open(io.BytesIO(data)).convert("RGB") for data in images]
    out = io.BytesIO()
    opened[0].save(out, "PDF", save_all=True, append_images=opened[1:], resolution=150)
    return out.getvalue()


async def stitch(
    db: AsyncSession,
    pages: list[dict[str, Any]],
    *,
    naming: dict[str, Any],
    owner_user_id: int | None,
    tags: set[str] | None = None,
    figures: dict[str, str] | None = None,
    page_labels: list[str] | None = None,
    source: str = "combined",
) -> dict[str, Any]:
    """One document from ``pages`` ({key, document_id, figures?}, in
    order), named ``naming``, filed under ``tags`` and everywhere a page
    that was a document was. Pages that were documents are retired, their
    bytes kept. ``page_labels`` name the pages where "Page 2" would not
    (a check's Front and Back).

    ``read`` is whether the pages still want reading: a page that was a
    document carries what was read off it."""
    from app.core.storage import get_storage
    from app.services.documents.queries import first_pages, tags_for_many
    from app.services.documents.service import DocumentService

    store = get_storage()
    images = [await store.get(page["key"]) for page in pages]
    if any(data is None for data in images):
        raise ValueError("A picture is no longer in storage; attach it again.")
    documents = DocumentService(db)
    made: Document = await documents.ingest(
        _pdf([data for data in images if data is not None]),
        title=naming["title"],
        media_type="application/pdf",
        owner_user_id=owner_user_id,
        source=source,
        page_count=len(pages),
    )
    made_id = int(made.id or 0)
    await documents.update(made_id, naming)
    kept: dict[str, str] = {}
    for page in pages:
        kept.update(page.get("figures") or {})
    await documents.add_figures(made_id, figures or kept)
    labels = set(tags or ())
    originals = [int(page["document_id"]) for page in pages if page.get("document_id")]
    read = await first_pages(db, originals) if originals else {}
    if originals:
        for found in (await tags_for_many(db, originals)).values():
            labels.update(found)
    for number, page in enumerate(pages, start=1):
        was = read.get(int(page["document_id"] or 0))
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
    if page_labels:
        made.meta_data = {**(made.meta_data or {}), "page_labels": page_labels}
        db.add(made)
    for label in sorted(labels):
        await documents.tag(made_id, label)
    await db.flush()
    return {"document_id": made_id, "retired": originals, "read": not originals}
