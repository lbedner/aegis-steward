"""Facts: what you can say about the subject, and how you know.

A page of its own because a fact outlives the case that gathered it -
what James's pension paid in August is true whether or not the renewal
that asked is still open - and because the provenance is half the row:
every control here exists to make the source as easy to record as the
number.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, dialog_done, where_from
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.facts import FactService, drawn
from app.services.matters.matters import MatterService
from app.services.matters.models import (
    FACT_ATTRIBUTES,
    FACT_PERIODS,
    FACT_PROVENANCE,
)
from app.services.matters.service import PartyService

SECTION = section("matters")
router = APIRouter(prefix=SECTION.path)

FACT = "/facts/{fact_id:int}"


def _cents(amount: str) -> int | None:
    """Dollars as typed to cents; an empty box is no figure at all.

    ``money_to_cents`` is the one parser - it already takes the dollar
    sign and the commas people paste off a statement, and reads blank as
    zero, which here means "they did not give a number" rather than
    "they gave zero".
    """
    from app.components.web_frontend.filters import money_to_cents

    if not (amount or "").strip():
        return None
    cents = money_to_cents(amount)
    if cents is None:
        raise ValueError(f"{amount!r} is not an amount.")
    return cents


async def _subjects(db: AsyncSession, matter_id: int) -> list[dict[str, Any]]:
    """Who a fact on this matter can be about: everyone in the case,
    then everybody else. A fact is about a PERSON - it is their money -
    so the case's own people lead."""
    matters = MatterService(db)
    inside = [party.id for _, party in await matters.participants(matter_id)]
    parties = await PartyService(db).find()
    ranked = sorted(parties, key=lambda p: (p.id not in inside, p.sort_name or p.name))
    return [{"id": p.id, "name": p.name} for p in ranked]


async def facts_for(db: AsyncSession, matter_id: int) -> list[dict[str, Any]]:
    """The facts gathered for this case, as the page draws them."""
    names = {p.id: p.name for p in await PartyService(db).find()}
    return [
        drawn(fact, names)
        for fact in await FactService(db).find(matter_id=matter_id)
    ]


async def _form(
    request: Request,
    db: AsyncSession,
    matter_id: int,
    status_code: int = 200,
    errors: list[str] | None = None,
    **typed: Any,
) -> Response:
    from app.services.documents.service import DocumentService
    from app.services.matters.models import matter_tag

    filed, _ = await DocumentService(db).list_documents(tag=matter_tag(matter_id))
    return dialog(
        request,
        "partials/matters/fact.html",
        status_code,
        matter_id=matter_id,
        path=SECTION.path,
        subjects=await _subjects(db, matter_id),
        attributes=FACT_ATTRIBUTES,
        periods=FACT_PERIODS,
        provenances=FACT_PROVENANCE,
        documents=[{"id": d.id, "name": d.title} for d in filed],
        errors=errors or [],
        typed=typed,
    )


@router.get("/{matter_id:int}/facts/new", include_in_schema=False)
async def new_fact(request: Request, matter_id: int) -> Response:
    async with get_async_session() as db:
        if await MatterService(db).get(matter_id) is None:
            raise HTTPException(status_code=404)
        return await _form(request, db, matter_id)


@router.post("/{matter_id:int}/facts/new", include_in_schema=False)
async def record_fact(
    request: Request,
    matter_id: int,
    subject_party_id: Annotated[str, Form()] = "",
    attribute: Annotated[str, Form()] = "gross_income",
    label: Annotated[str, Form()] = "",
    amount: Annotated[str, Form()] = "",
    period: Annotated[str, Form()] = "month",
    text_value: Annotated[str, Form()] = "",
    as_of: Annotated[str, Form()] = "",
    provenance: Annotated[str, Form()] = "stated",
    document_id: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
    source_note: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    verified: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """One claim, with where it came from."""
    typed = {
        "subject_party_id": subject_party_id,
        "attribute": attribute,
        "label": label,
        "amount": amount,
        "period": period,
        "text_value": text_value,
        "as_of": as_of,
        "provenance": provenance,
        "document_id": document_id,
        "page": page,
        "source_note": source_note,
        "source_url": source_url,
    }
    async with get_async_session() as db:
        if await MatterService(db).get(matter_id) is None:
            raise HTTPException(status_code=404)
        try:
            if not subject_party_id:
                raise ValueError("Say who the fact is about.")
            await FactService(db).record(
                subject_party_id=int(subject_party_id),
                matter_id=matter_id,
                attribute=attribute,
                label=label,
                value_cents=_cents(amount),
                period=period,
                text_value=text_value,
                as_of=date_type.fromisoformat(as_of) if as_of else None,
                provenance=provenance,
                document_id=int(document_id) if document_id else None,
                page=int(page) if page else None,
                source_note=source_note,
                source_url=source_url,
                verified=bool(verified),
                owner_user_id=owner_user_id,
            )
        except ValueError as exc:
            return await _form(request, db, matter_id, 422, [str(exc)], **typed)
        await db.commit()
    return dialog_done(f"{SECTION.path}/{matter_id}", "Recorded")


@router.post(FACT + "/verify", include_in_schema=False)
async def verify(request: Request, fact_id: int) -> Response:
    """Somebody checked it against the source. Never set by extraction:
    the whole point of the flag is that a person looked."""
    async with get_async_session() as db:
        facts = FactService(db)
        fact = await facts.get(fact_id)
        if fact is None:
            raise HTTPException(status_code=404)
        await facts.verify(fact_id, not fact.verified)
        await db.commit()
        matter_id = fact.matter_id
    return dialog_done(
        where_from(request, f"{SECTION.path}/{matter_id or ''}"),
        "Verified" if not fact.verified else "Unverified",
    )


@router.post(FACT + "/forget", include_in_schema=False)
async def forget(request: Request, fact_id: int) -> Response:
    """A fact recorded by mistake. A WRONG fact is superseded instead -
    this is for the row that should never have existed."""
    async with get_async_session() as db:
        facts = FactService(db)
        fact = await facts.get(fact_id)
        if fact is None:
            raise HTTPException(status_code=404)
        matter_id = fact.matter_id
        await facts.forget(fact_id)
        await db.commit()
    return dialog_done(
        where_from(request, f"{SECTION.path}/{matter_id or ''}"), "Forgotten"
    )
