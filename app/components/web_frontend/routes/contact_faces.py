"""A contact's faces: the tabs one contact is read through (#582).

The page was ten cards - how to reach them, family, sign-ins, policies,
accounts, cases, what they say and what is said about them, the letters,
the shelf, what was paid them - and a contact worth a page has most of
them filled. Each is a tab now, a page of its own, and every face draws
the same header and tabs from ``face``.

Almost none of this is new data. Facts name them as source or subject,
matters carry them as participants, requests name them as requester, an
account is held with them; the pages are where those are read together.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.documents import PAPER_COLUMNS, papers_on
from app.components.web_frontend.nav import contact_tabs, section
from app.components.web_frontend.rendering import or_404, render
from app.components.web_frontend.routes.filed import filed
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.reach import CONTACT_FIELDS, reach_lines
from app.services.matters.relations import relations_for
from app.services.matters.service import PartyService, party_tag

SECTION = section("contacts")
router = APIRouter(prefix=SECTION.path)


async def _held(
    db: AsyncSession, party_id: int, owner_user_id: int | None
) -> list[Any]:
    """Their accounts (a person) or the accounts held there (a place)."""
    from app.services.finance.service import (
        EVERYONE,
        accounts_page,
        institution_of,
        subject_of,
    )

    subject = await subject_of(db, party_id)
    if subject is not None:
        held, _ = await accounts_page(
            db,
            owner_user_id=owner_user_id,
            include_hidden=False,
            page=1,
            page_size=200,
            subject_id=subject.id,
        )
        return list(held)
    institution = await institution_of(db, party_id)
    if institution is None:
        return []
    everyone, _ = await accounts_page(
        db,
        owner_user_id=owner_user_id,
        include_hidden=False,
        page=1,
        page_size=500,
        subject_id=EVERYONE,
    )
    return [a for a in everyone if a.institution_id == institution.id]


async def face(
    db: AsyncSession, party_id: int, current: str, owner_user_id: int | None
) -> dict[str, Any]:
    """What every face of a contact draws: its header, and its tabs with
    what each holds. What the counts read is handed on, so a tab draws
    from it rather than asking again."""
    from app.services.matters.facts import FactService, drawn, place_book
    from app.services.matters.matters import MatterService

    party = or_404(await PartyService(db).get(party_id))
    places = await place_book(db)
    facts = FactService(db)
    says = [drawn(f, places) for f in await facts.find(source_party_id=party_id)]
    about = [drawn(f, places) for f in await facts.find(subject_party_id=party_id)]
    cases = [
        {"id": m.id, "title": m.title, "status": m.status, "role": role}
        for m, role in await MatterService(db).for_party(party_id)
    ]
    held = await _held(db, party_id, owner_user_id)
    papers = await papers_on(
        db, party_tag(party_id), f"{SECTION.path}/{party_id}/documents"
    )
    counts = {
        "accounts": len(held),
        "matters": len(cases),
        "facts": len(says) + len(about),
        "papers": len(papers),
    }
    tabs = contact_tabs(party_id, party.kind, current, counts)
    return {
        "section": SECTION,
        "path": SECTION.path,
        "party": party,
        # The tab showing, which a redraw asks for again.
        "here": next(t["href"] for t in tabs["sub_nav"] if t["key"] == current),
        "says": says,
        "about": about,
        "cases": cases,
        "held": held,
        "papers": papers,
        **tabs,
    }


def _page(request: Request, context: dict[str, Any]) -> Response:
    return render(request, "pages/contact.html", context)


@router.get("/{party_id:int}", include_in_schema=False)
async def overview(
    request: Request,
    party_id: int,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """How to reach them, their note, and (a person) their family."""
    async with get_async_session() as db:
        context = await face(db, party_id, "overview", owner_user_id)
        contact = context["party"].contact or {}
        related = (await relations_for(db, [party_id]))[party_id]
        kin = await PartyService(db).names([int(r["party_id"]) for r in related])
    return _page(
        request,
        {
            **context,
            "reach": [
                (label, contact.get(key))
                for key, label in CONTACT_FIELDS
                if contact.get(key)
            ],
            "lines": reach_lines(contact),
            "family": [{**r, "party": kin.get(int(r["party_id"]))} for r in related],
        },
    )


def _tab(key: str, describe: str) -> None:
    """A face that draws only what ``face`` already read."""

    async def page(
        request: Request,
        party_id: int,
        owner_user_id: int | None = Depends(get_owner_user_id),
    ) -> Response:
        async with get_async_session() as db:
            context = await face(db, party_id, key, owner_user_id)
        return _page(request, {**context, "paper_columns": list(PAPER_COLUMNS)})

    page.__doc__ = describe
    router.add_api_route(
        f"/{{party_id:int}}/{key}", page, methods=["GET"], include_in_schema=False
    )


_tab("logins", "Where they are logged into, and by whom (a place).")
_tab("cover", "The policies they wrote, or the ones that cover them.")
_tab("accounts", "Their accounts, or the ones held there.")
_tab("facts", "What they say, and what is said about them.")
_tab("papers", "Their paper: filed with them, whatever matter needs it.")


@router.get("/{party_id:int}/matters", include_in_schema=False)
async def matters(
    request: Request,
    party_id: int,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The cases they are in, and the letters they wrote."""
    from app.services.matters.requests import RequestService

    async with get_async_session() as db:
        context = await face(db, party_id, "matters", owner_user_id)
        letters = await RequestService(db).from_party(party_id)
    return _page(request, {**context, "letters": letters})


@router.get("/{party_id:int}/paid", include_in_schema=False)
async def paid(
    request: Request,
    party_id: int,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """What was paid them: the charges filed with the contact."""
    async with get_async_session() as db:
        context = await face(db, party_id, "paid", owner_user_id)
        spent = await filed(db, party_tag(party_id))
    return _page(request, {**context, "paid": spent})
