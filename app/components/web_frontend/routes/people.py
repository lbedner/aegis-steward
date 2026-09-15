"""People and organizations: the address book everything else points at.

A Settings tab rather than a section of its own. Institutions already
live here and are the same shape - a name, a way to reach it - and three
rows do not earn a place in the sidebar. When matters arrive and a party
becomes something you navigate TO rather than maintain, that changes.

Its own module rather than more of ``settings.py``, which is already at
its recorded size: the budget decides this, which is the point of having
one.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.nav import settings_nav as nav_context
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    render,
    where_from,
)
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.facts import web_address
from app.services.matters.models import PARTY_KINDS
from app.services.matters.service import PartyService

SECTION = section("settings")
router = APIRouter(prefix=SECTION.path)

PARTY_COLUMNS = (
    {"key": "sort_name", "label": "Filed under"},
    {"key": "name", "label": "Name", "kind": "open"},
    {"key": "kind", "label": "Kind"},
    {"key": "reach", "label": "How to reach them"},
)

# What a contact block may hold, in the order a letter carries it. A bag
# with a declared shape: the column stays JSON because a county office
# has a fax and a person has a mobile, but the FORM is not a free-text
# blob either.
CONTACT_FIELDS = (
    ("address", "Address"),
    ("phone", "Phone"),
    ("email", "Email"),
    # The website is what makes an organization a PLACE: a pension fund
    # is somewhere you log in, and a fact read off its portal wants to
    # point at the org rather than repeat the address every time.
    ("website", "Website"),
)


def _reach(contact: dict[str, Any] | None) -> str:
    """The one line a list can show. The rest is on the party."""
    contact = contact or {}
    return next(
        (str(contact[key]) for key, _label in CONTACT_FIELDS if contact.get(key)),
        "",
    )


def _row(party: Any) -> dict[str, Any]:
    return {
        "id": party.id,
        "sort_name": party.sort_name,
        "name": {
            "label": party.name,
            "url": f"{SECTION.path}/people/{party.id}",
        },
        "kind": party.kind.title(),
        "reach": _reach(party.contact),
    }


@router.get("/people", include_in_schema=False)
async def people(
    request: Request,
    q: str = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Everyone a matter, a document or an account can name."""
    async with get_async_session() as db:
        parties = await PartyService(db).find(owner_user_id=owner_user_id, q=q)
        return render(
            request,
            "pages/settings/people.html",
            {
                "section": SECTION,
                **nav_context("people"),
                "rows": [_row(p) for p in parties],
                "columns": list(PARTY_COLUMNS),
                "path": SECTION.path,
                "q": q,
                "kinds": PARTY_KINDS,
            },
        )


def _form(
    request: Request,
    *,
    errors: list[str],
    status_code: int = 200,
    party: Any = None,
    **values: Any,
) -> Response:
    return dialog(
        request,
        "partials/settings/party.html",
        status_code,
        party=party,
        kinds=PARTY_KINDS,
        contact_fields=list(CONTACT_FIELDS),
        errors=errors,
        **values,
    )


@router.get("/people/new", include_in_schema=False)
async def new_party(request: Request) -> Response:
    return _form(request, errors=[], name="", kind="person")


@router.get("/people/{party_id:int}", include_in_schema=False)
async def edit_party(request: Request, party_id: int) -> Response:
    async with get_async_session() as db:
        party = await PartyService(db).get(party_id)
    if party is None:
        return _form(request, errors=["That party is gone."], status_code=404)
    return _form(request, errors=[], party=party)


@router.post("/people/new", include_in_schema=False)
@router.post("/people/{party_id:int}", include_in_schema=False)
async def save_party(
    request: Request,
    party_id: int | None = None,
    name: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "person",
    sort_name: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Create or correct one party.

    ``sort_name`` is offered empty on a new party and filled on an edit,
    so the guess is visible and overrulable rather than hidden.
    """
    form = await request.form()
    contact = {
        key: str(form.get(key) or "").strip()
        for key, _label in CONTACT_FIELDS
        if str(form.get(key) or "").strip()
    }
    if "website" in contact:
        # Checked here, because it is rendered as a link: an href is a
        # place the reader clicks, and one parser owns that decision.
        try:
            contact["website"] = web_address(contact["website"]) or ""
        except ValueError as exc:
            return _form(
                request,
                errors=[str(exc)],
                status_code=422,
                party=None,
                name=name,
                kind=kind,
                sort_name=sort_name,
                note=note,
            )
    async with get_async_session() as db:
        parties = PartyService(db)
        try:
            if party_id is None:
                party = await parties.create(
                    name=name,
                    kind=kind,
                    owner_user_id=owner_user_id,
                    sort_name=sort_name,
                    contact=contact,
                    note=note,
                )
            else:
                party = await parties.update(
                    party_id,
                    {
                        "name": name,
                        "sort_name": sort_name,
                        "note": note,
                        "contact": contact,
                    },
                )
                if party is None:
                    return _form(request, errors=["That party is gone."], status_code=404)
        except ValueError as exc:
            return _form(
                request,
                errors=[str(exc)],
                status_code=422,
                name=name,
                kind=kind,
                sort_name=sort_name,
                note=note,
            )
        await db.commit()
        saved = party.name
    return dialog_done(
        where_from(request, f"{SECTION.path}/people"), f"Saved {saved}"
    )


@router.delete("/people/{party_id:int}", include_in_schema=False)
async def remove_party(request: Request, party_id: int) -> Response:
    """Soft delete: matters and documents point here."""
    async with get_async_session() as db:
        await PartyService(db).remove(party_id)
        await db.commit()
    return dialog_done(where_from(request, f"{SECTION.path}/people"), "Removed")
