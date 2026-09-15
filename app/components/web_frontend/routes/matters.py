"""Matters: the cases letters are episodes of.

Its own sidebar section, unlike People. A party is maintained; a matter
is navigated TO - it is the thing you open when a letter arrives with a
deadline on it, and it is where the documents, the requests and the
answers hang.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    render,
    where_from,
)
from app.components.web_frontend.routes.facts import facts_for
from app.components.web_frontend.routes.matter_papers import (
    PAPER_COLUMNS,
    matter_papers,
)
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.matters import MatterService, summarised
from app.services.matters.models import PARTICIPANT_ROLES
from app.services.matters.requests import RequestService
from app.services.matters.requests import drawn as drawn_request
from app.services.matters.service import PartyService

SECTION = section("matters")
router = APIRouter(prefix=SECTION.path)

MATTER_COLUMNS = (
    # A page, not a modal: a matter is worked for weeks, read beside a
    # letter and come back to, and none of that survives in a dialog.
    {"key": "title", "label": "Matter", "kind": "page"},
    {"key": "kind", "label": "Kind"},
    {"key": "reference", "label": "Their reference"},
    {"key": "who", "label": "With"},
    {"key": "state", "label": "", "kind": "status"},
)


def _row(matter: Any, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": matter.id,
        "title": {
            "label": matter.title,
            "url": f"{SECTION.path}/{matter.id}",
        },
        "kind": (matter.kind or "").title(),
        "reference": matter.reference or "",
        "who": names.get(matter.counterpart_party_id or -1, ""),
        "state": {
            "label": matter.status,
            "tone": "ok" if matter.status == "open" else "muted",
        },
    }


@router.get("", include_in_schema=False)
async def page(
    request: Request, owner_user_id: int | None = Depends(get_owner_user_id)
) -> Response:
    """Every case, open ones first."""
    async with get_async_session() as db:
        matters = await MatterService(db).find(owner_user_id=owner_user_id)
        names = {p.id: p.name for p in await PartyService(db).find()}
        return render(
            request,
            "pages/matters.html",
            {
                "section": SECTION,
                "rows": [_row(m, names) for m in matters],
                "columns": list(MATTER_COLUMNS),
                "path": SECTION.path,
            },
        )


@router.get("/new", include_in_schema=False)
async def new_matter(request: Request) -> Response:
    async with get_async_session() as db:
        parties = await PartyService(db).find()
    return dialog(
        request,
        "partials/matters/matter.html",
        matter=None,
        parties=[{"id": p.id, "name": p.name} for p in parties],
        roles=PARTICIPANT_ROLES,
        errors=[],
    )


@router.post("/new", include_in_schema=False)
async def create_matter(
    request: Request,
    title: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    reference: Annotated[str, Form()] = "",
    subject_party_id: Annotated[str, Form()] = "",
    counterpart_party_id: Annotated[str, Form()] = "",
    opened_on: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Open a case.

    A reference that already names a matter does NOT make a second one:
    the agency's own number is how two letters agree they are about the
    same thing, and a duplicate case is how the second letter gets lost.
    """
    async with get_async_session() as db:
        matters = MatterService(db)
        existing = await matters.by_reference(reference)
        if existing is not None:
            return dialog_done(
                f"{SECTION.path}/{existing.id}",
                f"{existing.title} already has that reference",
            )
        try:
            matter = await matters.open(
                title=title,
                kind=kind,
                reference=reference,
                subject_party_id=int(subject_party_id) if subject_party_id else None,
                counterpart_party_id=(
                    int(counterpart_party_id) if counterpart_party_id else None
                ),
                owner_user_id=owner_user_id,
                opened_on=date_type.fromisoformat(opened_on) if opened_on else None,
            )
        except ValueError as exc:
            parties = await PartyService(db).find()
            return dialog(
                request,
                "partials/matters/matter.html",
                422,
                matter=None,
                parties=[{"id": p.id, "name": p.name} for p in parties],
                roles=PARTICIPANT_ROLES,
                errors=[str(exc)],
                title=title,
                kind=kind,
                reference=reference,
            )
        # The subject and the counterpart are participants too: the
        # columns say who the case is ABOUT and who it is WITH, and the
        # participant list is what a page reads, so both have to be true.
        for party_id, role in (
            (matter.subject_party_id, "subject"),
            (matter.counterpart_party_id, "agency"),
        ):
            if party_id:
                await matters.add_participant(matter.id, party_id, role)
        await db.commit()
        opened = matter.id
    return dialog_done(f"{SECTION.path}/{opened}", "Opened")


@router.get("/{matter_id:int}", include_in_schema=False)
async def matter(request: Request, matter_id: int) -> Response:
    """One case: who is in it, and under what."""
    async with get_async_session() as db:
        found = await MatterService(db).get(matter_id)
        if found is None:
            raise HTTPException(status_code=404)
        drawn = await summarised(db, found)
        asked = [
            await drawn_request(db, one)
            for one in await RequestService(db).for_matter(matter_id)
        ]
        known = await facts_for(db, matter_id)
        papers = await matter_papers(db, matter_id)
    return render(
        request,
        "pages/matter.html",
        {
            "section": SECTION,
            "matter": drawn,
            "requests": asked,
            "facts": known,
            "papers": papers,
            "paper_columns": list(PAPER_COLUMNS),
            "path": SECTION.path,
        },
    )


@router.post("/{matter_id:int}/participants", include_in_schema=False)
async def add_participant(
    request: Request,
    matter_id: int,
    party_id: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "other",
) -> Response:
    async with get_async_session() as db:
        matters = MatterService(db)
        if await matters.get(matter_id) is None:
            raise HTTPException(status_code=404)
        if party_id:
            await matters.add_participant(matter_id, int(party_id), role)
            await db.commit()
    return dialog_done(
        where_from(request, f"{SECTION.path}/{matter_id}"), "Added"
    )
