"""The shelf: every document filed against a case.

Split from ``requests`` at the budget, and the seam is honest: paper
arrives before anybody knows which ask it answers, so putting it on the
case is its own act. Which item it satisfies is decided from the item.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.documents import file_upload, papers_on
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, dialog_done
from app.components.web_frontend.routes.requests import ACCEPTS
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.matters import MatterService
from app.services.matters.models import matter_tag

SECTION = section("matters")
router = APIRouter(prefix=SECTION.path)


# The shelf: every document filed against the case, answering an item or
# not. A letter's enclosures, the benefit statement somebody dug out of a
# drawer, the award letter from three years ago - paper arrives before
# anybody knows which ask it answers, and a shelf you cannot put a
# document on is a shelf nobody uses.
async def matter_papers(db: AsyncSession, matter_id: int) -> list[dict[str, Any]]:
    """The paper on this matter, shaped for the table."""
    return await papers_on(
        db, matter_tag(matter_id), f"{SECTION.path}/{matter_id}/documents"
    )


@router.get("/{matter_id:int}/documents/new", include_in_schema=False)
async def new_paper(request: Request, matter_id: int) -> Response:
    async with get_async_session() as db:
        if await MatterService(db).get(matter_id) is None:
            raise HTTPException(status_code=404)
    return dialog(
        request,
        "partials/matters/paper.html",
        post=f"{SECTION.path}/{matter_id}/documents/new",
        accepts=ACCEPTS,
        errors=[],
    )


@router.post("/{matter_id:int}/documents/new", include_in_schema=False)
async def add_paper(
    request: Request,
    matter_id: int,
    file: Annotated[UploadFile | None, File()] = None,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Put a document on the case without saying yet what it answers.

    Which ask it satisfies is a separate act, made from the item - and
    often not known at the moment somebody finds the paper.
    """

    async with get_async_session() as db:
        if await MatterService(db).get(matter_id) is None:
            raise HTTPException(status_code=404)
        document = await file_upload(
            db, file, owner_user_id=owner_user_id, tags=(matter_tag(matter_id),)
        )
        await db.commit()
    return dialog_done(f"{SECTION.path}/{matter_id}", f"Filed {document.title}")
