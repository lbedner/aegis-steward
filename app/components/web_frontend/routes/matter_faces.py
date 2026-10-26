"""A case's faces: the tabs one matter is read through (#582).

The case page stacked who is in it, every letter with its asks, the
facts, the shelf and the spending; five letters made it a page nobody
could find their way down. Each is a tab now, a page of its own, and
every face draws the same header and tabs from ``face`` so cycling them
never redraws a different case.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.documents import PAPER_COLUMNS
from app.components.web_frontend.nav import matter_tabs, section
from app.components.web_frontend.rendering import or_404, render
from app.components.web_frontend.routes.facts import facts_for
from app.components.web_frontend.routes.filed import filed
from app.components.web_frontend.routes.matter_papers import matter_papers
from app.core.db import get_async_session
from app.services.matters.answers import answer_sheet
from app.services.matters.drawing import drawn as drawn_request
from app.services.matters.matters import MatterService, summarised
from app.services.matters.requests import RequestService
from app.services.matters.service import matter_tag

SECTION = section("matters")
router = APIRouter(prefix=SECTION.path)


async def face(db: AsyncSession, matter_id: int, current: str) -> dict[str, Any]:
    """What every face of a case draws: its header, and its tabs with
    what each holds. What the counts read is handed on (``requests``,
    ``facts``, ``papers``, ``sheet``), so a tab draws from it rather
    than asking the database the same question twice."""
    found = or_404(await MatterService(db).get(matter_id))
    requests = await RequestService(db).for_matter(matter_id)
    facts = await facts_for(db, matter_id)
    papers = await matter_papers(db, matter_id)
    sheet = await answer_sheet(db, matter_id)
    counts = {
        "asked": sum(1 for one in requests if one.status == "open"),
        "facts": len(facts),
        "papers": len(papers),
        "answers": sheet["outstanding"],
    }
    return {
        "section": SECTION,
        "path": SECTION.path,
        "matter": await summarised(db, found),
        "matter_id": matter_id,
        "requests": requests,
        "facts": facts,
        "papers": papers,
        "sheet": sheet,
        **matter_tabs(matter_id, current, counts),
    }


def _page(request: Request, context: dict[str, Any]) -> Response:
    return render(request, "pages/matter.html", context)


@router.get("/{matter_id:int}", include_in_schema=False)
async def overview(request: Request, matter_id: int) -> Response:
    """The case at a glance: its note, who is in it, what is due next
    and when the next letter comes."""
    async with get_async_session() as db:
        context = await face(db, matter_id, "overview")
        # Only the open ones: what is due next is what is still asked.
        context["due"] = [
            await drawn_request(db, one)
            for one in context["requests"]
            if one.status == "open"
        ]
    return _page(request, context)


@router.get("/{matter_id:int}/asked", include_in_schema=False)
async def asked(request: Request, matter_id: int) -> Response:
    """Every letter and what it asked for."""
    async with get_async_session() as db:
        context = await face(db, matter_id, "asked")
        context["asked"] = [await drawn_request(db, one) for one in context["requests"]]
    return _page(request, context)


@router.get("/{matter_id:int}/facts", include_in_schema=False)
async def facts(request: Request, matter_id: int) -> Response:
    """What we can say, and how we know."""
    async with get_async_session() as db:
        context = await face(db, matter_id, "facts")
    return _page(request, context)


@router.get("/{matter_id:int}/papers", include_in_schema=False)
async def papers(request: Request, matter_id: int) -> Response:
    """The shelf: every document filed on the case."""
    async with get_async_session() as db:
        context = await face(db, matter_id, "papers")
    return _page(request, {**context, "paper_columns": list(PAPER_COLUMNS)})


@router.get("/{matter_id:int}/spent", include_in_schema=False)
async def spent(request: Request, matter_id: int) -> Response:
    """What the case has cost: the charges filed with it."""
    async with get_async_session() as db:
        context = await face(db, matter_id, "spent")
        context["spent"] = await filed(db, matter_tag(matter_id))
    return _page(request, context)
