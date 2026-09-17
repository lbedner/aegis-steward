"""What a letter asked for, and the paper on the case.

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

from app.components.web_frontend.documents import file_upload
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, dialog_done, where_from
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.matters import MatterService
from app.services.matters.models import ITEM_KINDS, matter_tag
from app.services.matters.requests import RequestService, asked_lines
from app.services.matters.requests import drawn as drawn_request
from app.services.matters.service import PartyService, party_or_new

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
    new_requester: Annotated[str, Form()] = "",
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
                    requester_party_id=await party_or_new(
                        db,
                        requester_party_id,
                        new_requester,
                        kind="organization",
                        owner_user_id=owner_user_id,
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


REQUEST = "/requests/{request_id:int}"


@router.get(REQUEST + "/items/new", include_in_schema=False)
async def new_item(request: Request, request_id: int) -> Response:
    """One more ask, on a request that already exists."""
    async with get_async_session() as db:
        service = RequestService(db)
        found = await service.get(request_id)
        if found is None:
            raise HTTPException(status_code=404)
        return dialog(
            request,
            "partials/matters/item_new.html",
            request_id=request_id,
            post=f"{SECTION.path}/requests/{request_id}/items/new",
            kinds=ITEM_KINDS,
            siblings=[
                {"id": one.id, "name": one.asked}
                for one in await service.items(request_id)
            ],
            errors=[],
            typed={},
        )


@router.post(REQUEST + "/items/new", include_in_schema=False)
async def add_item(
    request: Request,
    request_id: int,
    asked: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "document",
    ask: Annotated[str, Form()] = "",
    as_of: Annotated[str, Form()] = "",
    alternative_to: Annotated[str, Form()] = "",
) -> Response:
    async with get_async_session() as db:
        service = RequestService(db)
        found = await service.get(request_id)
        if found is None:
            raise HTTPException(status_code=404)
        try:
            await service.add_item(
                request_id,
                asked=asked,
                kind=kind,
                ask=ask,
                as_of=date_type.fromisoformat(as_of) if as_of else None,
                alternative_to=int(alternative_to) if alternative_to else None,
            )
        except ValueError as exc:
            return dialog(
                request,
                "partials/matters/item_new.html",
                422,
                request_id=request_id,
                post=f"{SECTION.path}/requests/{request_id}/items/new",
                kinds=ITEM_KINDS,
                siblings=[
                    {"id": one.id, "name": one.asked}
                    for one in await service.items(request_id)
                ],
                errors=[str(exc)],
                typed={"asked": asked, "kind": kind, "ask": ask, "as_of": as_of},
            )
        await db.commit()
    return dialog_done(
        where_from(request, f"{SECTION.path}/{found.matter_id}"), "Added"
    )


@router.get(REQUEST + "/letter", include_in_schema=False)
async def letter_form(request: Request, request_id: int) -> Response:
    """Which piece of paper this request came from."""
    from app.services.documents.service import DocumentService

    async with get_async_session() as db:
        found = await RequestService(db).get(request_id)
        if found is None:
            raise HTTPException(status_code=404)
        filed, _ = await DocumentService(db).list_documents(
            tag=matter_tag(found.matter_id)
        )
    return dialog(
        request,
        "partials/matters/letter.html",
        post=f"{SECTION.path}/requests/{request_id}/letter",
        accepts=ACCEPTS,
        documents=[{"id": d.id, "name": d.title} for d in filed],
        current=[found.document_id] if found.document_id else [],
        errors=[],
    )


@router.post(REQUEST + "/letter", include_in_schema=False)
async def set_letter(
    request: Request,
    request_id: int,
    document_id: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Cite the letter: pick one already filed, or add it now."""

    async with get_async_session() as db:
        service = RequestService(db)
        found = await service.get(request_id)
        if found is None:
            raise HTTPException(status_code=404)
        if file is not None and file.filename:
            document = await file_upload(
                db,
                file,
                owner_user_id=owner_user_id,
                tags=(matter_tag(found.matter_id),),
                kind="letter",
            )
            chosen = document.id
        elif document_id:
            chosen = int(document_id)
        else:
            raise HTTPException(status_code=400, detail="Pick a document or add one.")
        await service.cite(request_id, chosen)
        await db.commit()
    return dialog_done(
        where_from(request, f"{SECTION.path}/{found.matter_id}"), "Filed"
    )
