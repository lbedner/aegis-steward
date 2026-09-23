"""One ask: marking it, correcting it, and the paper that answers it.

Split from ``requests`` at the budget. That module is about the LETTER -
recording what it demanded, citing the page it came on; this one is
about a single demand inside it, which is where the work actually
happens.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.responses import Response

from app.components.web_frontend.documents import (
    DocumentForm,
    document_dialog,
    file_upload,
    save_document,
)
from app.components.web_frontend.filters import parse_date
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    where_from,
)
from app.components.web_frontend.routes.requests import (
    ACCEPTS,
    ITEM,
    _card,
    _matter_document,
)
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.models import ITEM_KINDS, matter_tag
from app.services.matters.requests import RequestService

SECTION = section("matters")
router = APIRouter(prefix=SECTION.path)


@router.post(ITEM + "/mark/{status}", include_in_schema=False)
async def mark_item(request: Request, item_id: int, status: str) -> Response:
    """Settle one item, and answer with the whole request."""
    async with get_async_session() as db:
        try:
            item = await RequestService(db).mark(item_id, status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        or_404(item)
        await db.commit()
        return await _card(request, db, item.request_id)


@router.get(ITEM + "/edit", include_in_schema=False)
async def edit_item(request: Request, item_id: int) -> Response:
    async with get_async_session() as db:
        item = await RequestService(db).item(item_id)
        or_404(item)
        return dialog(
            request,
            "partials/matters/item.html",
            item=item,
            kinds=ITEM_KINDS,
            post=f"{SECTION.path}{ITEM.replace('{item_id:int}', str(item_id))}/edit",
            errors=[],
        )


@router.post(ITEM + "/edit", include_in_schema=False)
async def save_item(
    request: Request,
    item_id: int,
    asked: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    ask: Annotated[str, Form()] = "",
    as_of: Annotated[str, Form()] = "",
) -> Response:
    """Correct the sentence, the kind of work, or what would satisfy it."""
    async with get_async_session() as db:
        requests = RequestService(db)
        item = await requests.item(item_id)
        or_404(item)
        try:
            await requests.amend(
                item_id,
                asked=asked,
                kind=kind,
                ask=ask,
                as_of=parse_date(as_of),
            )
        except ValueError as exc:
            return dialog(
                request,
                "partials/matters/item.html",
                422,
                item=item,
                kinds=ITEM_KINDS,
                post=(
                    f"{SECTION.path}{ITEM.replace('{item_id:int}', str(item_id))}/edit"
                ),
                errors=[str(exc)],
            )
        found = await requests.get(item.request_id)
        await db.commit()
    return dialog_done(
        where_from(request, f"{SECTION.path}/{found.matter_id if found else ''}"),
        "Saved",
    )


@router.get(ITEM + "/attach", include_in_schema=False)
async def attach_form(request: Request, item_id: int) -> Response:
    """Link the paper that answers this item, or add it.

    Both ways in one dialog because they are the same intent arrived at
    from two places: the document is already in the app, or it is on
    your desk.
    """
    from app.components.web_frontend.filters import short_date
    from app.services.documents.service import DocumentService

    async with get_async_session() as db:
        item = await RequestService(db).item(item_id)
        or_404(item)
        filed, _ = await DocumentService(db).list_documents()
    return dialog(
        request,
        "partials/matters/attach.html",
        item={"id": item.id, "asked": item.asked},
        post=f"{SECTION.path}{ITEM.replace('{item_id:int}', str(item_id))}/attach",
        accepts=ACCEPTS,
        documents=[
            {
                "id": d.id,
                "name": d.title,
                "fact": short_date(d.document_date or d.received_at),
            }
            for d in filed
        ],
        current=[item.document_id] if item.document_id else [],
        errors=[],
    )


@router.post(ITEM + "/attach", include_in_schema=False)
async def attach(
    request: Request,
    item_id: int,
    document_id: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Attach a filed document, or the one being added now.

    An added document is filed against the matter as well as the item:
    the item says what answered this demand, the matter tag is how the
    case's paper stays findable when the request is long settled.
    """
    from app.services.documents.service import DocumentService

    async with get_async_session() as db:
        requests = RequestService(db)
        item = await requests.item(item_id)
        or_404(item)
        found = await requests.get(item.request_id)
        or_404(found)
        documents = DocumentService(db)
        if file is not None and file.filename:
            document = await file_upload(db, file, owner_user_id=owner_user_id)
        elif document_id:
            document = await documents.get(int(document_id))
            or_404(document)
        else:
            raise HTTPException(status_code=400, detail="Pick a document or add one.")
        await documents.tag(document.id, matter_tag(found.matter_id))
        await requests.attach(item_id, document.id, document.title)
        await db.commit()
    return dialog_done(
        where_from(request, f"{SECTION.path}/{found.matter_id}"),
        f"Attached {document.title}",
    )


@router.post(ITEM + "/detach", include_in_schema=False)
async def detach(request: Request, item_id: int) -> Response:
    """Wrong paper: the item stands again."""
    async with get_async_session() as db:
        item = await RequestService(db).detach(item_id)
        or_404(item)
        await db.commit()
        return await _card(request, db, item.request_id)


DOCUMENT = "/{matter_id:int}/documents/{document_id:int}"


@router.get(DOCUMENT, include_in_schema=False)
async def document(request: Request, matter_id: int, document_id: int) -> Response:
    """The actual paper, from the line that asked for it."""
    async with get_async_session() as db:
        found = await _matter_document(db, matter_id, document_id)
        return await document_dialog(
            request, db, found, f"{SECTION.path}/{matter_id}/documents/{document_id}"
        )


@router.post(DOCUMENT, include_in_schema=False)
async def document_save(
    request: Request,
    matter_id: int,
    document_id: int,
    form: Annotated[DocumentForm, Form()],
) -> Response:
    """Save what we say about the paper. The bytes never change."""
    async with get_async_session() as db:
        found = await _matter_document(db, matter_id, document_id)
        errors = await save_document(db, document_id, form)
        if errors:
            return await document_dialog(
                request,
                db,
                found,
                f"{SECTION.path}/{matter_id}/documents/{document_id}",
                422,
                errors,
            )
    return dialog_done(
        where_from(request, f"{SECTION.path}/{matter_id}"),
        f"Saved {form.title.strip()}",
    )
