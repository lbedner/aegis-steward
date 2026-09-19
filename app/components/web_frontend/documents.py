"""A filed document in the one modal, wherever it is filed.

An account has paper and so does a matter, and "open the document" is
the same act in both places: the original on the left, what we say
about it on the right, the text that was read underneath. The routes
differ only in where the form posts and what a 404 means, so the dialog
and the save live here and both callers read them.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from fastapi import Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.rendering import dialog

# The API route that serves the bytes. One place, because the viewer,
# the "open the original" link and the fallback all point at it.
DOCUMENT_API = "/api/v1/documents"


def content_url(document_id: int) -> str:
    return f"{DOCUMENT_API}/{document_id}/content"


async def file_upload(
    db: AsyncSession,
    file: Any,
    *,
    owner_user_id: int | None,
    tags: tuple[str, ...] = (),
    kind: str = "other",
) -> Any:
    """The one way a form's file lands on the shelf: read, ingested as an
    upload, tagged where it belongs. No file is a 400 that says so; a
    file the store refuses is a 400 that says why."""
    from fastapi import HTTPException

    from app.services.documents.service import DocumentService

    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="Pick a file.")
    documents = DocumentService(db)
    try:
        document = await documents.ingest(
            await file.read(),
            title=file.filename,
            kind=kind,
            media_type=file.content_type,
            owner_user_id=owner_user_id,
            source="upload",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for tag in tags:
        await documents.tag(int(document.id), tag)
    return document


async def document_dialog(
    request: Request,
    db: AsyncSession,
    document: Any,
    post: str,
    status_code: int = 200,
    errors: list[str] | None = None,
    read_only: bool = False,
) -> Response:
    """The document beside what we say about it, in the one modal.

    Both halves in one body because they are read together: somebody
    opening a filed document is checking a figure against the page and
    correcting what it was filed as, and making that two dialogs is
    making them click twice to do one thing.
    """
    from app.components.web_frontend.filters import short_date
    from app.services.documents.domains.extraction.pages import how_read
    from app.services.documents.models import DOCUMENT_KINDS
    from app.services.documents.queries import pages_for
    from app.services.documents.service import DocumentService

    pages = await pages_for(db, document.id)
    tags = await DocumentService(db).tags_for(document.id)
    filed = await filed_under(db, {document.id: tags})
    return dialog(
        request,
        "partials/accounts/document.html",
        status_code,
        document=document,
        dated=short_date(document.document_date or document.received_at),
        content=content_url(document.id),
        filed=filed.get(document.id, []),
        filed_chips=[
            {"id": d["tag"], "name": d["label"]} for d in filed.get(document.id, [])
        ],
        places=await places(db),
        kinds=DOCUMENT_KINDS,
        post=post,
        read_only=read_only,
        # Read again lives with the document, wherever the dialog opened.
        reread=f"/documents/{document.id}/read",
        errors=errors or [],
        pages=[
            {
                "number": page.page_number,
                "read": page.status == "read",
                # How it was read, so a figure quoted off this page can
                # say where it came from.
                "method": page.method,
                "how": how_read(page),
                "text": page.text or "",
                "detail": page.detail or "",
            }
            for page in pages
        ],
    )


async def save_document(
    db: AsyncSession,
    document_id: int,
    *,
    title: str,
    kind: str,
    document_date: str,
    note: str,
    places: list[str] | None = None,
) -> list[str]:
    """Save what we SAY about a document, and give back what refused it.

    ``places`` is where it is filed, whole: the chips on the form. None
    means the form did not carry the field, and nothing is refiled.

    The bytes never change: a document is what arrived, and correcting
    it would make the record a lie. Errors come back as a list rather
    than an exception because every caller answers them the same way -
    by re-rendering the dialog with the page still beside the form.
    """
    from app.services.documents.service import DocumentService

    if not title.strip():
        return ["Give the document a title."]
    dated: date_type | None = None
    if document_date:
        try:
            dated = date_type.fromisoformat(document_date)
        except ValueError:
            return ["That date is not a date."]
    try:
        await DocumentService(db).update(
            document_id,
            {
                "title": title,
                "kind": kind,
                "document_date": dated,
                "note": note.strip() or None,
            },
        )
    except ValueError as exc:
        return [str(exc)]
    if places is not None:
        documents = DocumentService(db)
        wanted = {p for p in places if place_key(p)}
        current = {t for t in await documents.tags_for(document_id) if place_key(t)}
        for tag in wanted - current:
            await documents.tag(document_id, tag)
        for tag in current - wanted:
            await documents.untag(document_id, tag)
    await db.commit()
    return []


# The shelf: every document under one tag, shaped for the table. A
# matter's paper and a contact's paper are the same shelf pointed at a
# different tag, so the columns and the row are written once here.
PAPER_COLUMNS = (
    {"key": "title", "label": "Document", "kind": "open"},
    {"key": "kind", "label": "Kind"},
    {"key": "at", "label": "Dated"},
    {"key": "pages", "label": "Pages"},
)


def _homes() -> dict[str, tuple[Any, str, str, str]]:
    """Where a document can be filed: tag prefix -> (model, name field,
    section path, what to call it). Bound in a function, never at import."""
    from app.components.web_frontend.nav import section
    from app.services.finance.models.accounts import FinanceAccount
    from app.services.matters.models import Matter, Party

    return {
        "party": (Party, "name", section("contacts").path, "contact"),
        "matter": (Matter, "title", section("matters").path, "matter"),
        "account": (FinanceAccount, "name", section("accounts").path, "account"),
    }


def place_key(tag: str) -> tuple[str, int] | None:
    """``party:3`` as ``("party", 3)``; a bare label, or a home nobody
    defined, is None."""
    prefix, _, rest = tag.partition(":")
    return (prefix, int(rest)) if prefix in _homes() and rest.isdigit() else None


async def filed_under(
    db: AsyncSession, tags_by_document: dict[int, list[str]]
) -> dict[int, list[dict[str, str]]]:
    """Where each document is filed, as ``{label, url, tag}`` doors: a
    tag is a key (``party:3``) and a reader wants the name and the page.
    One query per kind of home for the whole shelf, never one per row."""
    from sqlmodel import select

    keyed = {
        k for tags in tags_by_document.values() for t in tags if (k := place_key(t))
    }
    door: dict[tuple[str, int], dict[str, str]] = {}
    for prefix, (model, field, path, _what) in _homes().items():
        ids = [i for p, i in keyed if p == prefix]
        if ids:
            for row in (await db.exec(select(model).where(model.id.in_(ids)))).all():
                door[(prefix, row.id)] = {
                    "label": getattr(row, field),
                    "url": f"{path}/{row.id}",
                    "tag": f"{prefix}:{row.id}",
                }
    return {
        document_id: [door[k] for t in tags if (k := place_key(t)) in door]
        for document_id, tags in tags_by_document.items()
    }


async def places(db: AsyncSession) -> list[dict[str, str]]:
    """Everywhere a document can be filed, as ``{id, name}`` options."""
    from sqlmodel import select

    options: list[dict[str, str]] = []
    for prefix, (model, field, _path, what) in _homes().items():
        rows = (await db.exec(select(model).order_by(getattr(model, field)))).all()
        options.extend(
            {"id": f"{prefix}:{row.id}", "name": f"{getattr(row, field)} ({what})"}
            for row in rows
        )
    return options


async def papers_on(db: AsyncSession, tag: str, open_base: str) -> list[dict[str, Any]]:
    """The paper filed under ``tag``, each row opening at ``open_base/<id>``."""
    from app.components.web_frontend.filters import short_date
    from app.components.web_frontend.glyphs import file_badge
    from app.services.documents.service import DocumentService

    documents, _ = await DocumentService(db).list_documents(tag=tag)
    return [
        {
            "title": {
                "label": d.title,
                "url": f"{open_base}/{d.id}",
                "badge": file_badge(d.media_type, d.title),
            },
            "kind": d.kind,
            "at": short_date(d.document_date or d.received_at),
            "pages": d.page_count or "",
            "import_batch_id": d.import_batch_id,
            "created_at": d.created_at,
        }
        for d in documents
    ]
