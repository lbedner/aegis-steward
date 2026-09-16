"""Sign-ins: how you get into someone else's account.

Inside the party dialog rather than a page of its own, because a login
is not a thing you navigate to - you are already looking at whose it is.
Every verb answers with the whole list (pattern 2): adding, editing and
revealing all change what the list says.

The password is never in a listing. ``reveal`` renders it into the one
row that asked, for as long as the dialog is open, and the next swap
takes it away again.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.facts import place_book
from app.services.matters.service import PartyService, party_or_new
from app.services.matters.signins import SignInService, drawn

SECTION = section("contacts")
router = APIRouter(prefix=SECTION.path)

SIGN_IN = "/signins/{sign_in_id:int}"


async def signins_for(db: AsyncSession, party_id: int) -> list[dict[str, Any]]:
    book = await place_book(db)
    return [drawn(one, book) for one in await SignInService(db).for_party(party_id)]


async def signins_at(db: AsyncSession, party_id: int) -> list[dict[str, Any]]:
    """Who signs in HERE: the other end of the same rows.

    An organization's page said "No sign-ins yet" while a sign-in
    pointed straight at it, because the list only ever read one side of
    the link.
    """
    names = {party.id: party.name for party in await PartyService(db).find()}
    book = await place_book(db)
    return [
        {**drawn(one, book), "whose": names.get(one.party_id, "")}
        for one in await SignInService(db).at_site(party_id)
    ]


async def _block(
    request: Request,
    db: AsyncSession,
    party_id: int,
    revealed: dict[int, str] | None = None,
    form: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    book = await place_book(db)
    party = await PartyService(db).get(party_id)
    return dialog(
        request,
        "partials/settings/signins.html",
        status_code,
        party_id=party_id,
        # A place is signed IN TO; a person signs in. The block says the
        # one that is true of this party, and its empty line matches.
        place=party is not None and party.kind == "organization",
        signins=await signins_for(db, party_id),
        visitors=await signins_at(db, party_id),
        # Somewhere with a website is somewhere you can be sent; the
        # rest of the address book is not a place to log in to.
        places=[
            {"id": one, "name": place["name"]}
            for one, place in book.items()
            if place["website"]
        ],
        revealed=revealed or {},
        form=form,
        errors=errors or [],
    )


@router.get("/{party_id:int}/signins", include_in_schema=False)
async def listing(request: Request, party_id: int) -> Response:
    async with get_async_session() as db:
        if await PartyService(db).get(party_id) is None:
            raise HTTPException(status_code=404)
        return await _block(request, db, party_id)


@router.get("/{party_id:int}/signins/new", include_in_schema=False)
async def new_signin(request: Request, party_id: int) -> Response:
    async with get_async_session() as db:
        if await PartyService(db).get(party_id) is None:
            raise HTTPException(status_code=404)
        return await _block(request, db, party_id, form={"id": None})


@router.get(SIGN_IN + "/edit", include_in_schema=False)
async def edit_signin(request: Request, sign_in_id: int) -> Response:
    """The details, never the password: a form that pre-fills a secret
    has put it in the page to save somebody one keystroke."""
    async with get_async_session() as db:
        sign_in = await SignInService(db).get(sign_in_id)
        if sign_in is None:
            raise HTTPException(status_code=404)
        return await _block(request, db, sign_in.party_id, form=drawn(sign_in))


@router.post("/{party_id:int}/signins/new", include_in_schema=False)
async def add_signin(
    request: Request,
    party_id: int,
    label: Annotated[str, Form()] = "",
    site_party_id: Annotated[str, Form()] = "",
    new_site: Annotated[str, Form()] = "",
    url: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
    secret: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    async with get_async_session() as db:
        if await PartyService(db).get(party_id) is None:
            raise HTTPException(status_code=404)
        try:
            await SignInService(db).add(
                party_id=party_id,
                label=label,
                site_party_id=await party_or_new(
                    db,
                    site_party_id,
                    new_site,
                    kind="organization",
                    owner_user_id=owner_user_id,
                ),
                url=url,
                username=username,
                secret=secret,
                note=note,
                owner_user_id=owner_user_id,
            )
        except ValueError as exc:
            return await _block(
                request,
                db,
                party_id,
                form={
                    "id": None,
                    "label": label,
                    "site_party_id": site_party_id,
                    "url": url,
                    "username": username,
                },
                errors=[str(exc)],
                status_code=422,
            )
        await db.commit()
        return await _block(request, db, party_id)


@router.post(SIGN_IN, include_in_schema=False)
async def save_signin(
    request: Request,
    sign_in_id: int,
    label: Annotated[str, Form()] = "",
    site_party_id: Annotated[str, Form()] = "",
    new_site: Annotated[str, Form()] = "",
    url: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
    secret: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
) -> Response:
    """A blank password box leaves the password alone.

    It cannot mean "clear it": the form never showed the old one, so
    treating empty as a deletion would lose a password to an edit of
    the username.
    """
    async with get_async_session() as db:
        service = SignInService(db)
        sign_in = await service.get(sign_in_id)
        if sign_in is None:
            raise HTTPException(status_code=404)
        try:
            await service.change(
                sign_in_id,
                {
                    "label": label,
                    "site_party_id": await party_or_new(
                        db, site_party_id, new_site, kind="organization"
                    ),
                    "url": url,
                    "username": username,
                    "note": note,
                },
                secret=secret or None,
            )
        except ValueError as exc:
            return await _block(
                request,
                db,
                sign_in.party_id,
                form=drawn(sign_in),
                errors=[str(exc)],
                status_code=422,
            )
        await db.commit()
        return await _block(request, db, sign_in.party_id)


@router.post(SIGN_IN + "/reveal", include_in_schema=False)
async def reveal(request: Request, sign_in_id: int) -> Response:
    """Show one password, because somebody asked for it."""
    async with get_async_session() as db:
        service = SignInService(db)
        sign_in = await service.get(sign_in_id)
        if sign_in is None:
            raise HTTPException(status_code=404)
        secret = await service.reveal(sign_in_id)
        return await _block(
            request,
            db,
            sign_in.party_id,
            revealed={sign_in_id: secret} if secret else {},
        )


@router.delete(SIGN_IN, include_in_schema=False)
async def forget(request: Request, sign_in_id: int) -> Response:
    async with get_async_session() as db:
        service = SignInService(db)
        sign_in = await service.get(sign_in_id)
        if sign_in is None:
            raise HTTPException(status_code=404)
        party_id = sign_in.party_id
        await service.forget(sign_in_id)
        await db.commit()
        return await _block(request, db, party_id)
