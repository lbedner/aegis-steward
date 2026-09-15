"""What a letter asked for: recording it, marking it off, and the paper
that answers it.

Its own module, and the seam is the same one ``documents.py`` cut out of
``accounts.py``: this knows the documents service and the matter page
does not. A matter is a case; a request is an obligation with a deadline
and a stack of paper, which is enough of a subject to own a file.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.documents import document_dialog, save_document
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, dialog_done, where_from
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.matters import MatterService
from app.services.matters.models import matter_tag
from app.services.matters.requests import RequestService, asked_lines
from app.services.matters.requests import drawn as drawn_request
from app.services.matters.service import PartyService

SECTION = section("matters")
router = APIRouter(prefix=SECTION.path)

ITEM = "/requests/items/{item_id:int}"
# What a letter arrives as. The same shapes the chat attachment path
# already reads, because a scan of the power of attorney is a scan
# whichever door it comes through.
ACCEPTS = "application/pdf,image/png,image/jpeg,image/webp,image/heic,image/tiff"


async def _parties(db: AsyncSession) -> list[dict[str, Any]]:
    return [{"id": p.id, "name": p.name} for p in await PartyService(db).find()]


async def _matter_document(db: AsyncSession, matter_id: int, document_id: int) -> Any:
    """The document, if it is filed against THIS matter.

    The URL names both, and the tag is what files it - so a number
    guessed into the path gets a 404 rather than somebody else's paper.
    """
    from app.services.documents.service import DocumentService

    documents = DocumentService(db)
    found = await documents.get(document_id)
    if found is None or matter_tag(matter_id) not in await documents.tags_for(
        document_id
    ):
        raise HTTPException(status_code=404)
    return found


async def _card(request: Request, db: AsyncSession, request_id: int) -> Response:
    """One request, redrawn (pattern 2).

    Every verb on an item answers with the whole request rather than the
    line it touched: settling an item changes the request's own status
    and its "1 of 3", and half an answer is how a page starts
    contradicting itself.
    """
    found = await RequestService(db).get(request_id)
    if found is None:
        raise HTTPException(status_code=404)
    return dialog(
        request,
        "partials/matters/request.html",
        asked=await drawn_request(db, found),
        path=SECTION.path,
    )


@router.get("/{matter_id:int}/requests/new", include_in_schema=False)
async def new_request(request: Request, matter_id: int) -> Response:
    async with get_async_session() as db:
        if await MatterService(db).get(matter_id) is None:
            raise HTTPException(status_code=404)
        parties = await _parties(db)
    return dialog(
        request,
        "partials/matters/request_new.html",
        matter_id=matter_id,
        path=SECTION.path,
        parties=parties,
        errors=[],
        asked="",
        due_on="",
        received_on="",
    )


@router.post("/{matter_id:int}/requests/new", include_in_schema=False)
async def record_request(
    request: Request,
    matter_id: int,
    asked: Annotated[str, Form()] = "",
    due_on: Annotated[str, Form()] = "",
    received_on: Annotated[str, Form()] = "",
    requester_party_id: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """A letter's demands, in the order the letter made them."""
    async with get_async_session() as db:
        if await MatterService(db).get(matter_id) is None:
            raise HTTPException(status_code=404)
        items = asked_lines(asked)
        errors = [] if items else ["Write at least one thing they asked for."]
        if not errors:
            try:
                await RequestService(db).record(
                    matter_id=matter_id,
                    items=items,
                    requester_party_id=(
                        int(requester_party_id) if requester_party_id else None
                    ),
                    received_on=(
                        date_type.fromisoformat(received_on) if received_on else None
                    ),
                    due_on=date_type.fromisoformat(due_on) if due_on else None,
                    owner_user_id=owner_user_id,
                )
            except ValueError as exc:
                errors = [str(exc)]
        if errors:
            return dialog(
                request,
                "partials/matters/request_new.html",
                422,
                matter_id=matter_id,
                path=SECTION.path,
                parties=await _parties(db),
                errors=errors,
                asked=asked,
                due_on=due_on,
                received_on=received_on,
            )
        await db.commit()
    return dialog_done(f"{SECTION.path}/{matter_id}", "Recorded")


@router.post(ITEM + "/mark/{status}", include_in_schema=False)
async def mark_item(request: Request, item_id: int, status: str) -> Response:
    """Settle one item, and answer with the whole request."""
    async with get_async_session() as db:
        try:
            item = await RequestService(db).mark(item_id, status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404)
        await db.commit()
        return await _card(request, db, item.request_id)


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
        if item is None:
            raise HTTPException(status_code=404)
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
        if item is None:
            raise HTTPException(status_code=404)
        found = await requests.get(item.request_id)
        if found is None:
            raise HTTPException(status_code=404)
        documents = DocumentService(db)
        if file is not None and file.filename:
            data = await file.read()
            try:
                document = await documents.ingest(
                    data,
                    title=file.filename,
                    media_type=file.content_type,
                    owner_user_id=owner_user_id,
                    source="upload",
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        elif document_id:
            document = await documents.get(int(document_id))
            if document is None:
                raise HTTPException(status_code=404)
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
        if item is None:
            raise HTTPException(status_code=404)
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
    title: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "other",
    document_date: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
) -> Response:
    """Save what we say about the paper. The bytes never change."""
    async with get_async_session() as db:
        found = await _matter_document(db, matter_id, document_id)
        errors = await save_document(
            db,
            document_id,
            title=title,
            kind=kind,
            document_date=document_date,
            note=note,
        )
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
        where_from(request, f"{SECTION.path}/{matter_id}"), f"Saved {title.strip()}"
    )
