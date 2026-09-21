"""The Documents section: everything on file, wherever it was filed from.

A letter lands on a matter, a statement on an account, a screenshot in
the chat - and until now the only place to see them all at once was the
operator's dashboard. This is the reader's shelf: every document, what
it was filed as, whether it has been read, and the one dialog that
shows the original beside the text that was read off it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from starlette.responses import Response

from app.components.web_frontend.documents import (
    document_dialog,
    file_upload,
    filed_under,
    save_document,
)
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    render,
    templates,
    where_from,
    with_toast,
)
from app.components.web_frontend.routes.requests import ACCEPTS
from app.core.db import get_async_session
from app.core.storage import get_storage
from app.services.documents.domains.extraction.dispatch import start_extraction
from app.services.finance.deps import get_owner_user_id
from app.services.mail.jobs import start_mail_import
from app.services.mail.parse import is_mail_export

SECTION = section("documents")
router = APIRouter(prefix=SECTION.path)

COLUMNS = (
    {"key": "title", "label": "Document", "kind": "open"},
    {"key": "kind", "label": "Kind"},
    {"key": "at", "label": "Dated"},
    {"key": "pages", "label": "Pages"},
    {"key": "filed", "label": "Filed under", "kind": "links"},
)


def _row(document: Any, filed: list[dict[str, str]]) -> dict[str, Any]:
    from app.components.web_frontend.filters import short_date
    from app.components.web_frontend.glyphs import file_badge

    return {
        "id": document.id,
        "title": {
            "label": document.title,
            "url": f"{SECTION.path}/{document.id}",
            "badge": file_badge(
                document.media_type, document.title, source=document.source
            ),
        },
        "kind": document.kind,
        "at": short_date(document.document_date or document.received_at),
        "pages": document.page_count or "",
        "filed": filed,
    }


@router.get("", include_in_schema=False)
async def page(
    request: Request,
    q: str = "",
    kind: str = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Everything on file, newest first, narrowed where it stands."""
    from app.services.documents import queries
    from app.services.documents.models import DOCUMENT_KINDS
    from app.services.documents.service import DocumentService

    async with get_async_session() as db:
        documents, _total = await DocumentService(db).list_documents(
            owner_user_id=owner_user_id, kind=kind or None, page_size=200
        )
        # ponytail: the match is in Python over one page of 200; a
        # search column on the query when the shelf outgrows a page.
        #
        # The title AND what it arrived as: a renamed document is still
        # looked for by the name the bank gave the download, and that is
        # half the point of keeping it.
        if q:
            documents = [d for d in documents if _matches(d, q)]
        tags = await queries.tags_for_many(db, [d.id for d in documents])
        filed = await filed_under(db, tags)
        return render(
            request,
            "pages/documents.html",
            {
                "section": SECTION,
                "path": SECTION.path,
                "rows": [_row(d, filed.get(d.id, [])) for d in documents],
                "columns": list(COLUMNS),
                "q": q,
                "kind": kind,
                "kinds": DOCUMENT_KINDS,
            },
        )


def _matches(document: Any, q: str) -> bool:
    """A document the reader means by this word."""
    said = q.casefold()
    return (
        said in (document.title or "").casefold()
        or said in (document.filename or "").casefold()
    )


async def _filed(db: Any, document_id: int) -> Any:
    from app.services.documents.service import DocumentService

    document = await DocumentService(db).get(document_id)
    or_404(document)
    return document


@router.get("/new", include_in_schema=False)
async def new_document(request: Request) -> Response:
    from app.services.documents.models import DOCUMENT_KINDS

    return dialog(
        request,
        "partials/documents/upload.html",
        post=f"{SECTION.path}/new",
        accepts=ACCEPTS,
        kinds=DOCUMENT_KINDS,
        errors=[],
    )


@router.post("/new", include_in_schema=False)
async def upload(
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    kind: Annotated[str, Form()] = "other",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """File a document that belongs to nothing yet. Tagging it to a
    matter or an account is done from there.

    A mailbox export is not a document: it is a job that files many.
    The bytes go to storage and the answer is the follower (pattern 5),
    exactly as the finance import answers - the messages are read on
    the worker, and each attachment lands on this shelf.
    """
    if file is not None and is_mail_export(file.filename):
        storage_key = await get_storage().put(
            await file.read(), content_type=file.content_type
        )
        job_id = await start_mail_import(
            storage_key, file_name=str(file.filename), owner_user_id=owner_user_id
        )
        return templates.TemplateResponse(
            request=request,
            name="partials/imports/started.html",
            context={"job_id": job_id, "file_name": file.filename},
        )

    async with get_async_session() as db:
        document = await file_upload(db, file, owner_user_id=owner_user_id, kind=kind)
        await db.commit()
    return dialog_done(SECTION.path, f"Filed {document.title}")


@router.get("/{document_id:int}", include_in_schema=False)
async def document(
    request: Request, document_id: int, reading: bool = False
) -> Response:
    """The original beside its details and the text read off each page.

    ``?reading=1`` drops the form. It is how an approval card opens the
    paper it read: somebody checking a proposed title against the
    letterhead must not be able to type a different one behind the card
    that is about to overwrite it.
    """
    async with get_async_session() as db:
        found = await _filed(db, document_id)
        return await document_dialog(
            request,
            db,
            found,
            f"{SECTION.path}/{document_id}",
            read_only=reading,
        )


@router.post("/{document_id:int}", include_in_schema=False)
async def save(
    request: Request,
    document_id: int,
    title: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "other",
    document_date: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    place: Annotated[list[str], Form()] = [],
    place_sent: Annotated[str, Form()] = "",
) -> Response:
    async with get_async_session() as db:
        found = await _filed(db, document_id)
        errors = await save_document(
            db,
            document_id,
            title=title,
            kind=kind,
            document_date=document_date,
            note=note,
            places=place if place_sent else None,
        )
        if errors:
            return await document_dialog(
                request, db, found, f"{SECTION.path}/{document_id}", 422, errors
            )
        await db.commit()
    return dialog_done(where_from(request, SECTION.path), "Saved")


@router.get("/{document_id:int}/delete", include_in_schema=False)
async def delete_confirm(request: Request, document_id: int) -> Response:
    """Ask first: there is no undo, and the bytes go with it."""
    async with get_async_session() as db:
        found = await _filed(db, document_id)
    return dialog(
        request,
        "partials/matters/forget.html",
        title=f"Delete {found.title}?",
        body="The document, the text read off it, and its labels all go. "
        "Anything filed against it lets go of it. There is no undo.",
        url=f"{SECTION.path}/{document_id}",
        method="delete",
    )


@router.delete("/{document_id:int}", include_in_schema=False)
async def delete(
    request: Request,
    document_id: int,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The rows in one transaction; the bytes after it commits, so a
    crash between the two leaves an orphan blob and never a row that
    names paper that is gone."""
    from app.services.documents.domains.shelf.destroy import destroy

    async with get_async_session() as db:
        orphaned = or_404(await destroy(db, document_id, owner_user_id=owner_user_id))
        await db.commit()
    storage = get_storage()
    for key in orphaned:
        await storage.delete(key)
    return dialog_done(where_from(request, SECTION.path), "Deleted")


@router.post("/{document_id:int}/read", include_in_schema=False)
async def read_again(request: Request, document_id: int) -> Response:
    """Read every page again, and SHOW it happening.

    Six pages of a scan is a model call each. This answered with a 204
    and a toast, so the dialog sat there looking broken while the work
    ran and there was no way to tell a job that had started from one
    that had not - while the job id it needed was in hand and pattern 5
    was built for exactly this (2026-09-19).
    """
    async with get_async_session() as db:
        found = await _filed(db, document_id)
    job_id = await start_extraction(document_id, owner_user_id=None, force=True)
    return with_toast(
        templates.TemplateResponse(
            request=request,
            name="partials/documents/reading.html",
            context={"job_id": job_id, "title": found.title},
        ),
        f"Reading {found.title} again",
    )
