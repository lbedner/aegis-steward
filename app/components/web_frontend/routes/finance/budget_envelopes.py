"""Envelopes: crediting them, spending from them, and moving between.

Split out of ``budget.py`` at the 500-line budget.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.backend.api.finance.planning import (
    create_envelope,
    envelope_response,
    update_envelope,
)
from app.components.web_frontend.filters import cents_to_input, money, money_to_cents
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog,
    dialog_done,
    with_toast,
)
from app.components.web_frontend.routes.finance.budget_display import (
    CADENCES,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.planning.envelopes import envelope_metadata
from app.services.finance.models import FinanceAccount
from app.services.finance.schemas import (
    EnvelopeCreate,
    EnvelopeUpdate,
)
from app.services.finance.service import FinanceService

SECTION = section("budget")
router = APIRouter(prefix=SECTION.path)


# --- envelopes ---------------------------------------------------------------------


async def _envelope(
    service: FinanceService, account_id: int, owner_user_id: int | None
) -> FinanceAccount:
    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    if account is None or envelope_metadata(account.metadata_) is None:
        raise HTTPException(status_code=404)
    return account


async def _envelope_card(
    request: Request,
    service: FinanceService,
    account: FinanceAccount,
    oob: bool = False,
) -> Response:
    await service.db.commit()
    await service.db.refresh(account)
    return dialog(
        request,
        "partials/budget/envelope_card.html",
        envelope=await envelope_response(service.db, account),
        oob=oob,
        path=SECTION.path,
    )


def _move_dialog(
    request: Request,
    account: FinanceAccount,
    verb: str,
    errors: list[str],
    status_code: int = 200,
) -> Response:
    return dialog(
        request,
        "partials/budget/move.html",
        status_code,
        title=f"{verb.title()} {account.name}",
        action=f"{SECTION.path}/envelopes/{account.id}/{verb}",
        label=verb.title(),
        note=True,
        errors=errors,
    )


@router.post("/envelopes/{account_id:int}/edit", include_in_schema=False)
async def edit_envelope(
    request: Request,
    account_id: int,
    monthly_credit: Annotated[str, Form()] = "",
    cadence: Annotated[str, Form()] = "monthly",
    auto_credit: Annotated[str, Form()] = "",
    tag: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _envelope(service, account_id, owner_user_id)
    values = {
        **await _envelope_values(service, account),
        "monthly_credit": monthly_credit,
        "cadence": cadence,
        "auto_credit": auto_credit,
        "tag": tag,
    }
    credit = money_to_cents(monthly_credit)
    if credit is None or cadence not in {c["id"] for c in CADENCES}:
        return _envelope_editor(
            request,
            account,
            values,
            ["Amounts are in dollars; pick weekly or monthly."],
            422,
        )
    await update_envelope(
        account_id,
        EnvelopeUpdate(
            monthly_credit=credit or None,
            auto_credit=auto_credit == "on",
            cadence=cadence,
            # The whole form is the envelope's state: an empty field
            # stops it paying for a tag (#240).
            tag=tag,
        ),  # type: ignore[arg-type]
        service=service,
        owner_user_id=owner_user_id,
    )
    response = await _envelope_card(request, service, account, oob=True)
    return close_dialog(with_toast(response, f"Saved {account.name}."))


@router.get("/envelopes/{account_id:int}/{verb}", include_in_schema=False)
async def envelope_move_form(
    request: Request,
    account_id: int,
    verb: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _envelope(service, account_id, owner_user_id)
    if verb == "edit":
        return _envelope_editor(
            request, account, await _envelope_values(service, account), []
        )
    if verb not in ("credit", "spend"):
        raise HTTPException(status_code=404)
    return _move_dialog(request, account, verb, [])


@router.post("/envelopes/{account_id:int}/{verb}", include_in_schema=False)
async def envelope_move(
    request: Request,
    account_id: int,
    verb: str,
    amount: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _envelope(service, account_id, owner_user_id)
    if verb not in ("credit", "spend"):
        raise HTTPException(status_code=404)
    cents = money_to_cents(amount)
    if cents is None or cents <= 0:
        return _move_dialog(
            request, account, verb, ["Enter an amount in dollars."], 422
        )
    move = service.credit_envelope if verb == "credit" else service.spend_from_envelope
    await move(account_id, amount=cents, owner_user_id=owner_user_id, note=note or None)
    response = await _envelope_card(request, service, account, oob=True)
    return close_dialog(with_toast(response, f"{verb.title()}: {money(cents)}."))


async def _envelope_values(
    service: FinanceService | None, account: FinanceAccount | None
) -> dict[str, str]:
    from app.services.finance.domains.planning.envelope_tags import tag_name

    if account is None or service is None:
        return {
            "name": "",
            "monthly_credit": "",
            "cadence": "monthly",
            "starting_balance": "",
            "auto_credit": "",
            "tag": "",
        }
    meta = envelope_metadata(account.metadata_)
    assert meta is not None
    return {
        "name": account.name,
        "monthly_credit": cents_to_input(meta.monthly_credit),
        "cadence": meta.cadence,
        "starting_balance": "",
        "auto_credit": "on" if meta.auto_credit else "",
        "tag": await tag_name(service.db, meta) or "",
    }


def _envelope_editor(
    request: Request,
    account: FinanceAccount | None,
    values: dict[str, str],
    errors: list[str],
    status_code: int = 200,
) -> Response:
    return dialog(
        request,
        "partials/budget/envelope_editor.html",
        status_code,
        envelope=account,
        values=values,
        errors=errors,
        cadences=CADENCES,
        path=SECTION.path,
    )


@router.get("/envelopes/new", include_in_schema=False)
async def new_envelope_form(request: Request) -> Response:
    return _envelope_editor(request, None, await _envelope_values(None, None), [])


@router.post("/envelopes/new", include_in_schema=False)
async def create_envelope_route(
    request: Request,
    name: Annotated[str, Form()] = "",
    monthly_credit: Annotated[str, Form()] = "",
    cadence: Annotated[str, Form()] = "monthly",
    starting_balance: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    values = {
        "name": name,
        "monthly_credit": monthly_credit,
        "cadence": cadence,
        "starting_balance": starting_balance,
        "auto_credit": "",
    }
    credit = money_to_cents(monthly_credit)
    start = money_to_cents(starting_balance)
    errors = []
    if not name.strip():
        errors.append("Give the envelope a name.")
    if credit is None or start is None or cadence not in {c["id"] for c in CADENCES}:
        errors.append("Amounts are in dollars; pick weekly or monthly.")
    if errors:
        return _envelope_editor(request, None, values, errors, 422)
    envelope = await create_envelope(
        EnvelopeCreate(
            name=name.strip(),
            monthly_credit=credit or None,
            cadence=cadence,
            starting_balance=start or 0,
        ),  # type: ignore[arg-type]
        service=service,
        owner_user_id=owner_user_id,
    )
    await service.db.commit()
    return dialog_done(f"{SECTION.path}?tab=envelopes", f"Added {envelope.name}.")


@router.delete("/envelopes/{account_id:int}", include_in_schema=False)
async def remove_envelope(
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _envelope(service, account_id, owner_user_id)
    await service.soft_delete_account(account_id, owner_user_id=owner_user_id)
    await service.db.commit()
    return close_dialog(
        with_toast(Response(status_code=200), f"Removed {account.name}.")
    )
