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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.responses import Response

from app.components.web_frontend.documents import file_upload
from app.components.web_frontend.nav import contacts_tabs, section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    render,
    where_from,
    with_toast,
)
from app.core.db import get_async_session
from app.services.ai.service.trace import CHANGE_MARKER
from app.services.finance.deps import get_owner_user_id
from app.services.matters.facts import web_address
from app.services.matters.reach import (
    CONTACT_FIELDS,
    CONTACT_LINES,
    one_home,
    reach_lines,
)
from app.services.matters.service import PARTY_KINDS, PartyService, party_tag

SECTION = section("contacts")
router = APIRouter(prefix=SECTION.path)

PARTY_COLUMNS = (
    {"key": "sort_name", "label": "Filed under"},
    {"key": "name", "label": "Name", "kind": "page", "phone": "primary"},
    {"key": "reach", "label": "How to reach them"},
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
            "url": f"{SECTION.path}/{party.id}",
            # A page, not a dialog: a contact is worked - logged into,
            # written to, read off - and that does not fit a modal.
        },
        "reach": _reach(party.contact),
    }


async def _listing(
    request: Request, kind: str, q: str, owner_user_id: int | None
) -> Response:
    """One kind's list (#582), and how many of each the tabs say."""
    async with get_async_session() as db:
        everyone = await PartyService(db).find(owner_user_id=owner_user_id)
        found = await PartyService(db).find(owner_user_id=owner_user_id, q=q)
    counts = {k: sum(1 for p in everyone if p.kind == k) for k in PARTY_KINDS}
    return render(
        request,
        "pages/contacts.html",
        {
            "section": SECTION,
            "rows": [_row(p) for p in found if p.kind == kind],
            "columns": list(PARTY_COLUMNS),
            "path": SECTION.path,
            "here": f"{SECTION.path}/organizations"
            if kind == "organization"
            else SECTION.path,
            "kind": kind,
            "q": q,
            **contacts_tabs(kind, counts),
        },
    )


@router.get("", include_in_schema=False)
async def people(
    request: Request,
    q: str = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The people: the subjects of cases, family, whoever signs in."""
    return await _listing(request, "person", q, owner_user_id)


@router.get("/organizations", include_in_schema=False)
async def organizations(
    request: Request,
    q: str = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The places: agencies, funds, facilities, banks."""
    return await _listing(request, "organization", q, owner_user_id)


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
        lines=reach_lines(party.contact if party else None),
        errors=errors,
        **values,
    )


@router.get("/new", include_in_schema=False)
async def new_party(request: Request, kind: str = "person") -> Response:
    """Opened from a tab, it starts as that tab's kind."""
    return _form(
        request, errors=[], name="", kind=kind if kind in PARTY_KINDS else "person"
    )


@router.get("/{party_id:int}/edit", include_in_schema=False)
async def edit_party(request: Request, party_id: int) -> Response:
    async with get_async_session() as db:
        party = await PartyService(db).get(party_id)
    if party is None:
        return _form(request, errors=["They are gone."], status_code=404)
    return _form(request, errors=[], party=party)


@router.post("/{party_id:int}/look-up", include_in_schema=False)
async def look_up(request: Request, party_id: int) -> Response:
    """What their paper and the web say, as one card - opened right here.

    The card comes to the reader rather than waiting in Review to be
    chased down (2026-09-24). Nothing found is a 204 and a toast: there
    is nothing to decide, so no dialog opens.
    """
    from app.services.matters.lookup import propose_look_up

    async with get_async_session() as db:
        party = or_404(await PartyService(db).get(party_id))
    card = await propose_look_up(get_async_session, party_id)
    if card is None:
        return with_toast(
            Response(status_code=204), f"Nothing new found for {party.name}.", "info"
        )
    return dialog(
        request,
        "partials/contacts/looked_up.html",
        party=party,
        marker={"kind": CHANGE_MARKER, "id": card.id},
    )


@router.post("/new", include_in_schema=False)
@router.post("/{party_id:int}", include_in_schema=False)
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
    contact: dict[str, Any] = {
        key: str(form.get(key) or "").strip()
        for key, _label in CONTACT_FIELDS
        if str(form.get(key) or "").strip()
    }
    # Parallel lists, paired by position. A row whose value was emptied
    # is how a line goes away: the form is a statement of what there is,
    # not a list of deletions.
    said = [
        {"label": str(label), "value": str(value)}
        for label, value in zip(
            form.getlist("line_label"), form.getlist("line_value"), strict=False
        )
    ]
    if lines := reach_lines({CONTACT_LINES: said}):
        contact[CONTACT_LINES] = [
            {"label": label, "value": value} for label, value in lines
        ]
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
            # The same rule the two contact cards obey: the form is the
            # third way into this column, and a rule two writers of three
            # enforce is a rule with a door left open.
            one_home(contact, note)
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
                    return _form(request, errors=["They are gone."], status_code=404)
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
    return dialog_done(where_from(request, SECTION.path), f"Saved {saved}")


@router.delete("/{party_id:int}", include_in_schema=False)
async def remove_party(request: Request, party_id: int) -> Response:
    """Soft delete: matters and documents point here."""
    async with get_async_session() as db:
        await PartyService(db).remove(party_id)
        await db.commit()
    return dialog_done(where_from(request, SECTION.path), "Removed")


@router.get("/{party_id:int}/documents/new", include_in_schema=False)
async def new_paper(request: Request, party_id: int) -> Response:
    from app.components.web_frontend.routes.requests import ACCEPTS

    async with get_async_session() as db:
        party = await PartyService(db).get(party_id)
    or_404(party)
    return dialog(
        request,
        "partials/matters/paper.html",
        post=f"{SECTION.path}/{party_id}/documents/new",
        accepts=ACCEPTS,
        blurb=f"Filed with {party.name}: theirs, whatever matter later needs it.",
        errors=[],
    )


@router.post("/{party_id:int}/documents/new", include_in_schema=False)
async def add_paper(
    request: Request,
    party_id: int,
    file: Annotated[UploadFile | None, File()] = None,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Paper that is theirs - a statement, a benefit letter - filed
    against the contact so the next matter finds it on the shelf."""

    async with get_async_session() as db:
        if await PartyService(db).get(party_id) is None:
            raise HTTPException(status_code=404)
        document = await file_upload(
            db, file, owner_user_id=owner_user_id, tags=(party_tag(party_id),)
        )
        await db.commit()
    return dialog_done(f"{SECTION.path}/{party_id}/papers", f"Filed {document.title}")


@router.get("/{party_id:int}/documents/{document_id:int}", include_in_schema=False)
async def paper(request: Request, party_id: int, document_id: int) -> Response:
    """One of their documents: the original beside what was read."""
    from app.components.web_frontend.documents import document_dialog
    from app.services.documents.service import DocumentService

    async with get_async_session() as db:
        documents = DocumentService(db)
        found = await documents.get(document_id)
        if found is None or party_tag(party_id) not in await documents.tags_for(
            document_id
        ):
            raise HTTPException(status_code=404)
        return await document_dialog(request, db, found, f"/documents/{document_id}")
