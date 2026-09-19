"""The story of a case, in date order, and the events that leave no paper.

Its own module beside ``matter_papers`` for the same reason that one is:
the case page is where the work happens, and this is the case READ BACK.
Nothing here writes anything except ``matter_event``, because everything
else on the page is derived from rows some other page already owns.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.web_frontend.nav import matter_tabs, section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    render,
)
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.finance.utils import current_date
from app.services.matters.answers import answer_sheet
from app.services.matters.matters import MatterService
from app.services.matters.models import MATTER_EVENT_KINDS
from app.services.matters.timeline import MatterEventService, timeline

SECTION = section("matters")
router = APIRouter(prefix=SECTION.path)


def _links_to(moment: dict[str, Any], matter_id: int) -> str:
    """Where a moment goes, for the one kind that goes anywhere.

    Paper has a page of its own. Everything else on the story is a row
    on the case page, one tab over - and a link that moves the reader to
    another face of the thing they are already looking at is navigation
    dressed up as information. It was tried: the header is the same, the
    tabs are the same, and the only thing that changes is the content
    below, so following one reads as having gone nowhere (2026-09-18).

    The ids are still on the moment (``request_id``, ``item_id``,
    ``fact_id``) for whoever wants to SHOW what is behind one in place.
    """
    if moment["kind"] == "paper" and moment.get("document_id"):
        return f"{SECTION.path}/{matter_id}/documents/{moment['document_id']}"
    return ""


@router.get("/{matter_id:int}/timeline", include_in_schema=False)
async def matter_timeline(request: Request, matter_id: int) -> Response:
    """The case as a sequence: what arrived, what was answered, what was
    said on the phone."""
    async with get_async_session() as db:
        found = or_404(await MatterService(db).get(matter_id))
        moments = await timeline(db, matter_id)
        outstanding = (await answer_sheet(db, matter_id))["outstanding"]
    return render(
        request,
        "pages/matter_timeline.html",
        {
            "section": SECTION,
            "path": SECTION.path,
            **matter_tabs(matter_id, "timeline", outstanding),
            "matter": found,
            "matter_id": matter_id,
            "moments": [
                {**moment, "url": _links_to(moment, matter_id)} for moment in moments
            ],
        },
    )


@router.get("/{matter_id:int}/events/new", include_in_schema=False)
async def new_event(request: Request, matter_id: int) -> Response:
    async with get_async_session() as db:
        or_404(await MatterService(db).get(matter_id))
    return _event_dialog(request, matter_id, errors=[])


def _event_dialog(
    request: Request,
    matter_id: int,
    *,
    errors: list[str],
    occurred_at: str = "",
    kind: str = "call",
    summary: str = "",
    status_code: int = 200,
) -> Response:
    return dialog(
        request,
        "partials/matters/event.html",
        post=f"{SECTION.path}/{matter_id}/events",
        kinds=MATTER_EVENT_KINDS,
        occurred_at=occurred_at or current_date().isoformat(),
        kind=kind,
        summary=summary,
        errors=errors,
        status_code=status_code,
    )


@router.post("/{matter_id:int}/events", include_in_schema=False)
async def add_event(
    request: Request,
    matter_id: int,
    occurred_at: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "note",
    summary: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """What happened that left nothing behind: a call, a mailing, a visit."""
    async with get_async_session() as db:
        if await MatterService(db).get(matter_id) is None:
            raise HTTPException(status_code=404)
        try:
            # The day it happened, not the day it was typed: somebody
            # enters Tuesday's call on Friday.
            when = date.fromisoformat(occurred_at) if occurred_at else current_date()
            await MatterEventService(db).add(
                matter_id=matter_id,
                occurred_at=when,
                kind=kind,
                summary=summary,
                owner_user_id=owner_user_id,
            )
        except ValueError as exc:
            return _event_dialog(
                request,
                matter_id,
                errors=[str(exc)],
                occurred_at=occurred_at,
                kind=kind,
                summary=summary,
                status_code=422,
            )
        await db.commit()
    return dialog_done(
        f"{SECTION.path}/{matter_id}/timeline",
        "Added",
    )


@router.get("/{matter_id:int}/events/{event_id:int}/forget", include_in_schema=False)
async def forget_event_confirm(
    request: Request, matter_id: int, event_id: int
) -> Response:
    """Ask first. A typed line is easy to lose and there is no undo."""
    async with get_async_session() as db:
        event = or_404(await MatterEventService(db).get(event_id))
    return dialog(
        request,
        "partials/matters/forget.html",
        title="Remove this?",
        body=event.summary,
        url=f"{SECTION.path}/{matter_id}/events/{event_id}",
        method="delete",
    )


@router.delete("/{matter_id:int}/events/{event_id:int}", include_in_schema=False)
async def forget_event(request: Request, matter_id: int, event_id: int) -> Response:
    """Deleting an event changes nothing else: nothing points at one."""
    async with get_async_session() as db:
        event = or_404(await MatterEventService(db).get(event_id))
        if event.matter_id != matter_id:
            raise HTTPException(status_code=404)
        await MatterEventService(db).remove(event_id)
        await db.commit()
    return dialog_done(f"{SECTION.path}/{matter_id}/timeline", "Removed")
