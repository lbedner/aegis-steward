"""Policies on a contact's page: what an insurer wrote, what covers a
person, and the claims made on each.

One block, swapped whole by every verb (pattern 2), the way sign-ins
are: adding a policy and recording a claim both change what the block
says, and both forms open inside it rather than in the modal, because
you are already looking at whose policy it is.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from markupsafe import Markup
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.documents import file_upload
from app.components.web_frontend.filters import money_to_cents, parse_date
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, hx_swap, or_404
from app.core.db import get_async_session
from app.core.formatting import iso_date, payee_label
from app.services.finance.deps import get_owner_user_id
from app.services.insurance.models import CLAIM_STATUSES, POLICY_KINDS
from app.services.insurance.service import InsuranceService
from app.services.matters.models import party_tag
from app.services.matters.service import PartyService

SECTION = section("contacts")
router = APIRouter(prefix=SECTION.path)
POLICY = "/{party_id:int}/policies/{policy_id:int}"

CLAIM_COLUMNS = (
    {"key": "at", "label": "Visit"},
    {"key": "who", "label": "For", "kind": "contact", "wrap": True},
    # Provider and claim number step aside on a narrow screen: the money
    # and the EOB are what a glance is for.
    {
        "key": "provider",
        "label": "Provider",
        "kind": "contact",
        "wrap": True,
        "hide_below": "xl",
    },
    {"key": "number", "label": "Claim", "wrap": True, "hide_below": "xl"},
    {"key": "billed", "label": "Billed", "kind": "money", "align": "right"},
    {"key": "allowed", "label": "Allowed", "kind": "money", "align": "right"},
    {"key": "paid", "label": "Insurer paid", "kind": "money", "align": "right"},
    {"key": "owes", "label": "You owe", "kind": "money", "align": "right"},
    {"key": "eob", "label": "EOB", "kind": "open", "wrap": True},
    # The charge that paid the provider, or blank: the link between what
    # the EOB said and what the ledger shows leaving.
    {"key": "settled", "label": "Paid", "kind": "action"},
)

# The numbers a policy carries, drawn as rows in this order.
POLICY_ROWS = (
    ("policy_number", "Policy number"),
    ("member_id", "Member ID"),
    ("group_id", "Group ID"),
    ("effective_on", "Effective"),
    ("renews_on", "Renews"),
)


def _iso(value: date | None) -> str:
    return iso_date(value) or ""


def _terms(raw: str) -> dict[str, str]:
    """``Label: value`` per line, as the plan states them."""
    terms: dict[str, str] = {}
    for line in raw.splitlines():
        label, sep, value = line.partition(":")
        if sep and label.strip() and value.strip():
            terms[label.strip()] = value.strip()
    return terms


async def _drawn(
    db: AsyncSession, party_id: int, policy: Any, names: dict[int, str], owner: bool
) -> dict[str, Any]:
    """One policy as the page draws it. ``owner`` says the page is the
    insurer's, where claims are recorded; a person's page reads only."""
    from app.components.web_frontend.glyphs import file_badge
    from app.services.finance.domains.planning.recurring import streams
    from app.services.matters.requests import titles

    service = InsuranceService(db)
    claims = await service.claims_of(int(policy.id))
    eobs = await titles(db, [c.document_id for c in claims])
    paid = await _payments(db, [c.paid_transaction_id for c in claims])
    premium = None
    if policy.premium_stream_id is not None:
        stream = await streams.get_recurring(db, policy.premium_stream_id, None)
        premium = stream.name if stream else None
    return {
        "id": policy.id,
        "kind": policy.kind,
        "name": policy.name or f"{policy.kind.title()} policy",
        "insurer_id": policy.insurer_party_id,
        "insurer": names.get(policy.insurer_party_id, ""),
        "covered": [
            {"id": i, "name": names.get(i, f"contact {i}")}
            for i in policy.covered_party_ids or []
        ],
        "rows": [
            (label, _iso(v) if isinstance(v, date) else v)
            for key, label in POLICY_ROWS
            if (v := getattr(policy, key))
        ]
        + list((policy.terms or {}).items()),
        "premium": premium,
        "path": SECTION.path,
        "note": policy.note,
        "owes": await service.patient_owes_total(int(policy.id)),
        "claims": [
            {
                "id": c.id,
                "at": _iso(c.service_on),
                # The charge that paid it, or the verb that picks one.
                "settled": paid.get(c.paid_transaction_id, "")
                if c.paid_transaction_id or not owner
                else {
                    "label": "Mark paid",
                    "hx": hx_swap(
                        f"{SECTION.path}/{party_id}/policies/{policy.id}/claims/{c.id}/paid",
                        "#policies",
                    )
                    + Markup(" data-mark-paid"),
                },
                "who": {
                    "id": c.covered_party_id,
                    "label": names.get(c.covered_party_id, ""),
                },
                "provider": {
                    "id": c.provider_party_id,
                    "label": names.get(c.provider_party_id, ""),
                }
                if c.provider_party_id
                else None,
                # The status rides with the number only when it is news.
                "number": (c.claim_number or "")
                + (f" · {c.status}" if c.status != "processed" else ""),
                "billed": c.billed_cents,
                "allowed": c.allowed_cents,
                "paid": c.insurer_paid_cents,
                "owes": c.patient_owes_cents,
                # The column says EOB; the cell is the file's mark and a
                # short word, because a filename is one unbreakable string
                # that pushes the rest of the row off the card.
                "eob": {
                    "label": "Open",
                    "badge": file_badge(
                        eobs.get(c.document_id, {}).get("media_type"),
                        eobs.get(c.document_id, {}).get("title", ""),
                    ),
                    "url": f"{SECTION.path}/{policy.insurer_party_id}/documents/{c.document_id}",
                }
                if c.document_id
                else None,
            }
            for c in claims
        ],
        "claim_post": f"{SECTION.path}/{party_id}/policies/{policy.id}/claims/new"
        if owner
        else None,
    }


async def _payments(db: AsyncSession, ids: list[int | None]) -> dict[int, str]:
    """The paying charges as one line each - date and account - in ONE
    query per kind, never one per claim."""
    from sqlmodel import col, select

    from app.services.finance.domains.ledger.queries.accounts import account_names
    from app.services.finance.models import FinanceTransaction

    wanted = [i for i in ids if i]
    if not wanted:
        return {}
    rows = (
        await db.exec(
            select(FinanceTransaction).where(col(FinanceTransaction.id).in_(wanted))
        )
    ).all()
    named = await account_names(db, [t.account_id for t in rows])
    return {int(t.id): f"{_iso(t.date_)} · {named.get(t.account_id, '')}" for t in rows}


async def _candidates(
    db: AsyncSession, claim_id: int, owner_user_id: int | None
) -> list[dict[str, Any]]:
    """The picker's options: the same shortlist Illiana reads."""
    from sqlmodel import col, select

    from app.components.web_frontend.filters import money
    from app.services.finance.models import FinanceAccount

    rows = await InsuranceService(db).claim_candidates(
        claim_id, owner_user_id=owner_user_id
    )
    accounts = (
        (
            await db.exec(
                select(FinanceAccount).where(
                    col(FinanceAccount.id).in_({t.account_id for t in rows})
                )
            )
        ).all()
        if rows
        else []
    )
    named = {int(a.id): a.name for a in accounts}
    return [
        {
            "id": t.id,
            "name": f"{_iso(t.date_)} · {payee_label(None, t.merchant_name, t.name)} · "
            f"{money(abs(t.amount), 'USD')} · {named.get(t.account_id, '')}",
        }
        for t in rows
    ]


async def _block(
    request: Request,
    db: AsyncSession,
    party_id: int,
    form: dict[str, Any] | None = None,
    claim_form: dict[str, Any] | None = None,
    paid_form: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    party = await PartyService(db).get(party_id)
    or_404(party)
    service = InsuranceService(db)
    place = party.kind == "organization"
    policies = (
        await service.policies_of(party_id)
        if place
        else await service.policies_covering(party_id)
    )
    names = await PartyService(db).names()
    return dialog(
        request,
        "partials/contacts/policies.html",
        status_code,
        path=SECTION.path,
        party_id=party_id,
        place=place,
        policies=[await _drawn(db, party_id, p, names, place) for p in policies],
        people=await PartyService(db).options(kind="person"),
        kinds=POLICY_KINDS,
        statuses=CLAIM_STATUSES,
        claim_columns=list(CLAIM_COLUMNS),
        form=form,
        claim_form=claim_form,
        paid_form=paid_form,
        errors=errors or [],
    )


@router.get("/{party_id:int}/policies", include_in_schema=False)
async def listing(request: Request, party_id: int) -> Response:
    async with get_async_session() as db:
        return await _block(request, db, party_id)


@router.get("/{party_id:int}/policies/new", include_in_schema=False)
async def new_policy(request: Request, party_id: int) -> Response:
    async with get_async_session() as db:
        return await _block(request, db, party_id, form={})


@router.post("/{party_id:int}/policies/new", include_in_schema=False)
async def add_policy(
    request: Request,
    party_id: int,
    kind: Annotated[str, Form()] = "dental",
    name: Annotated[str, Form()] = "",
    policy_number: Annotated[str, Form()] = "",
    member_id: Annotated[str, Form()] = "",
    group_id: Annotated[str, Form()] = "",
    effective_on: Annotated[str, Form()] = "",
    renews_on: Annotated[str, Form()] = "",
    covered: Annotated[list[str], Form()] = [],
    covered_sent: Annotated[str, Form()] = "",
    terms: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    form = {
        "kind": kind,
        "name": name,
        "policy_number": policy_number,
        "member_id": member_id,
        "group_id": group_id,
        "effective_on": effective_on,
        "renews_on": renews_on,
        "covered": [c for c in covered if c.isdigit()],
        "terms": terms,
        "note": note,
    }
    async with get_async_session() as db:
        try:
            await InsuranceService(db).create_policy(
                insurer_party_id=party_id,
                covered_party_ids=[int(c) for c in form["covered"]],
                kind=kind,
                name=name,
                policy_number=policy_number.strip() or None,
                member_id=member_id.strip() or None,
                group_id=group_id.strip() or None,
                effective_on=parse_date(effective_on),
                renews_on=parse_date(renews_on),
                terms=_terms(terms),
                note=note,
                owner_user_id=owner_user_id,
            )
        except ValueError as exc:
            return await _block(
                request, db, party_id, form=form, errors=[str(exc)], status_code=422
            )
        await db.commit()
        return await _block(request, db, party_id)


@router.get(POLICY + "/claims/new", include_in_schema=False)
async def new_claim(request: Request, party_id: int, policy_id: int) -> Response:
    async with get_async_session() as db:
        return await _block(request, db, party_id, claim_form={"policy_id": policy_id})


@router.post(POLICY + "/claims/new", include_in_schema=False)
async def record_claim(
    request: Request,
    party_id: int,
    policy_id: int,
    covered_party_id: Annotated[str, Form()] = "",
    service_on: Annotated[str, Form()] = "",
    provider_party_id: Annotated[str, Form()] = "",
    claim_number: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "processed",
    billed: Annotated[str, Form()] = "",
    allowed: Annotated[str, Form()] = "",
    insurer_paid: Annotated[str, Form()] = "",
    patient_owes: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The EOB is filed with the insurer as it is recorded: the claim
    says what it settled, the tag is how the paper stays findable."""

    form = {
        "policy_id": policy_id,
        "covered_party_id": covered_party_id,
        "service_on": service_on,
        "provider_party_id": provider_party_id,
        "claim_number": claim_number,
        "status": status,
        "billed": billed,
        "allowed": allowed,
        "insurer_paid": insurer_paid,
        "patient_owes": patient_owes,
        "note": note,
    }
    cents = {
        k: money_to_cents(v)
        for k, v in (
            ("billed", billed),
            ("allowed", allowed),
            ("insurer_paid", insurer_paid),
            ("patient_owes", patient_owes),
        )
    }
    async with get_async_session() as db:
        errors: list[str] = []
        if any(v is None for v in cents.values()):
            errors.append("An amount is not a number.")
        if not covered_party_id.isdigit():
            errors.append("Say who the visit was for.")
        if not service_on:
            errors.append("Give the date of the visit.")
        if errors:
            return await _block(
                request, db, party_id, claim_form=form, errors=errors, status_code=422
            )
        document_id = None
        if file is not None and file.filename:
            document = await file_upload(
                db, file, owner_user_id=owner_user_id, tags=(party_tag(party_id),)
            )
            document_id = int(document.id)
        try:
            await InsuranceService(db).record_claim(
                policy_id=policy_id,
                covered_party_id=int(covered_party_id),
                service_on=date.fromisoformat(service_on),
                provider_party_id=int(provider_party_id)
                if provider_party_id.isdigit()
                else None,
                claim_number=claim_number.strip() or None,
                status=status,
                billed_cents=cents["billed"] or 0,
                allowed_cents=cents["allowed"] or 0,
                insurer_paid_cents=cents["insurer_paid"] or 0,
                patient_owes_cents=cents["patient_owes"] or 0,
                document_id=document_id,
                note=note,
                owner_user_id=owner_user_id,
            )
        except ValueError as exc:
            return await _block(
                request,
                db,
                party_id,
                claim_form=form,
                errors=[str(exc)],
                status_code=422,
            )
        await db.commit()
        return await _block(request, db, party_id)


CLAIM = POLICY + "/claims/{claim_id:int}"


@router.get(CLAIM + "/paid", include_in_schema=False)
async def pick_payment(
    request: Request,
    party_id: int,
    policy_id: int,
    claim_id: int,
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    async with get_async_session() as db:
        return await _block(
            request,
            db,
            party_id,
            paid_form={
                "claim_id": claim_id,
                "candidates": await _candidates(db, claim_id, owner_user_id),
            },
        )


@router.post(CLAIM + "/paid", include_in_schema=False)
async def mark_paid(
    request: Request,
    party_id: int,
    policy_id: int,
    claim_id: int,
    transaction_id: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    async with get_async_session() as db:
        try:
            if not transaction_id.isdigit():
                raise ValueError("Pick the charge that paid it.")
            await InsuranceService(db).mark_paid(
                claim_id, int(transaction_id), owner_user_id=owner_user_id
            )
        except ValueError as exc:
            return await _block(
                request,
                db,
                party_id,
                paid_form={
                    "claim_id": claim_id,
                    "candidates": await _candidates(db, claim_id, owner_user_id),
                },
                errors=[str(exc)],
                status_code=422,
            )
        await db.commit()
        return await _block(request, db, party_id)
