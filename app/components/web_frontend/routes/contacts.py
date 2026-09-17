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

from app.components.web_frontend.documents import PAPER_COLUMNS, file_upload, papers_on
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    render,
    where_from,
)
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.facts import web_address
from app.services.matters.models import CONTACT_FIELDS, PARTY_KINDS, party_tag
from app.services.matters.service import PartyService

SECTION = section("contacts")
router = APIRouter(prefix=SECTION.path)

PARTY_COLUMNS = (
    {"key": "sort_name", "label": "Filed under"},
    {"key": "name", "label": "Name", "kind": "page"},
    {"key": "kind", "label": "Kind"},
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
        "kind": party.kind.title(),
        "reach": _reach(party.contact),
    }


@router.get("", include_in_schema=False)
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
            "pages/contacts.html",
            {
                "section": SECTION,
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


@router.get("/new", include_in_schema=False)
async def new_party(request: Request) -> Response:
    return _form(request, errors=[], name="", kind="person")


@router.get("/{party_id:int}/edit", include_in_schema=False)
async def edit_party(request: Request, party_id: int) -> Response:
    async with get_async_session() as db:
        party = await PartyService(db).get(party_id)
    if party is None:
        return _form(request, errors=["They are gone."], status_code=404)
    return _form(request, errors=[], party=party)


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


@router.get("/{party_id:int}", include_in_schema=False)
async def contact(
    request: Request,
    party_id: int,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """One contact: everything that points at them, gathered.

    Almost none of this is new data. Facts name them as source or
    subject, matters carry them as participants, requests name them as
    requester, an account is held with them - the page is where those
    are read together. The one thing that is theirs alone is the paper
    filed against them, and that is the shelf at the bottom.
    """
    from app.services.finance.domains.ledger.queries.accounts import (
        EVERYONE,
        accounts_page,
    )
    from app.services.finance.domains.ledger.subjects import institution_of, subject_of
    from app.services.matters.facts import FactService, drawn, place_book
    from app.services.matters.matters import MatterService
    from app.services.matters.requests import RequestService

    async with get_async_session() as db:
        party = await PartyService(db).get(party_id)
        or_404(party)
        places = await place_book(db)
        facts = FactService(db)
        says = [drawn(f, places) for f in await facts.find(source_party_id=party_id)]
        about = [drawn(f, places) for f in await facts.find(subject_party_id=party_id)]
        cases = [
            {"id": m.id, "title": m.title, "status": m.status, "role": role}
            for m, role in await MatterService(db).for_party(party_id)
        ]
        letters = await RequestService(db).from_party(party_id)
        subject = await subject_of(db, party_id)
        institution = await institution_of(db, party_id)
        held: list[Any] = []
        if subject is not None:
            held, _ = await accounts_page(
                db,
                owner_user_id=owner_user_id,
                include_hidden=False,
                page=1,
                page_size=200,
                subject_id=subject.id,
            )
        elif institution is not None:
            everyone, _ = await accounts_page(
                db,
                owner_user_id=owner_user_id,
                include_hidden=False,
                page=1,
                page_size=500,
                subject_id=EVERYONE,
            )
            held = [a for a in everyone if a.institution_id == institution.id]
        papers = await papers_on(
            db, party_tag(party_id), f"{SECTION.path}/{party_id}/documents"
        )
        return render(
            request,
            "pages/contact.html",
            {
                "section": SECTION,
                "path": SECTION.path,
                "party": party,
                "reach": [
                    (label, (party.contact or {}).get(key))
                    for key, label in CONTACT_FIELDS
                    if (party.contact or {}).get(key)
                ],
                "cases": cases,
                "letters": letters,
                "says": says,
                "about": about,
                "held": held,
                "papers": papers,
                "paper_columns": list(PAPER_COLUMNS),
            },
        )


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
    return dialog_done(f"{SECTION.path}/{party_id}", f"Filed {document.title}")


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
