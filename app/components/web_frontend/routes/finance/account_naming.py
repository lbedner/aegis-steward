"""What an account is CALLED, and WHOSE money it holds.

Split from ``account_manage`` at the budget, and the seam is a real one:
reconciling and removing an account are things you do to its money,
while this is the account's identity - the name you call it, the numbers
it is known by, and the person whose money it is.

Whose money used to be asked once, when the account was created, which
every IMPORTED account skips. A father's checking account therefore read
as the household's, sat in its totals, and could not answer the county
asking what he holds - with no door but SQL to say otherwise
(2026-09-18).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from app.components.backend.api.finance.accounts import update_account
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    where_from,
)
from app.components.web_frontend.routes.finance import subjects
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger.numbers import set_number
from app.services.finance.models import FinanceAccount
from app.services.finance.schemas import AccountUpdate
from app.services.finance.service import FinanceService
from app.services.matters.service import party_or_new

SECTION = section("accounts")
router = APIRouter(prefix=SECTION.path)


async def _account(
    service: FinanceService, account_id: int, owner_user_id: int | None
) -> FinanceAccount:
    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    or_404(account)
    return account


async def _routed(service: FinanceService, account: Any, routing: str) -> bool:
    """Write the routing number to the BANK the account is held with.
    False when it cannot be one; nothing is written then."""
    from app.services.finance.domains.ledger.numbers import aba_ok
    from app.services.finance.domains.ledger.queries.accounts import institution_by_id

    if not aba_ok(routing):
        return False
    bank = await institution_by_id(service.db, account.institution_id)
    if bank is not None:
        bank.routing_number = routing.strip()
        service.db.add(bank)
    return True


async def _naming(
    request: Request,
    service: FinanceService,
    account: Any,
    name: str,
    reference: str,
    routing: str | None = None,
    refused: str = "",
    whose: str | None = None,
) -> Response:
    """The naming dialog, opened or refused. One home, because the two
    differ only by what went wrong.

    The routing number is drawn here beside the account number, because
    that is the pair people think in - but it belongs to the BANK, so
    it is read from there and only offered once a bank is set.
    """
    from app.services.finance.domains.ledger.queries.accounts import institution_by_id

    if routing is None and account.institution_id:
        bank = await institution_by_id(service.db, account.institution_id)
        routing = (bank.routing_number or "") if bank else ""
    return dialog(
        request,
        "partials/accounts/rename.html",
        422 if refused else 200,
        account=account,
        name=name,
        reference=reference,
        routing=routing,
        people=await subjects.people(service),
        whose=(
            int(whose)
            if whose and str(whose).isdigit()
            else await subjects.holder(service, account)
        ),
        errors=[refused] if refused else [],
    )


@router.get("/{account_id:int}/rename", include_in_schema=False)
async def rename_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return await _naming(
        request, service, account, account.name, account.reference or ""
    )


@router.post("/{account_id:int}/rename", include_in_schema=False)
async def rename(
    request: Request,
    account_id: int,
    name: Annotated[str, Form()] = "",
    reference: Annotated[str, Form()] = "",
    account_number: Annotated[str, Form()] = "",
    routing_number: Annotated[str, Form()] = "",
    whose: Annotated[str, Form()] = "",
    new_whose: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    label = name.strip()
    if not label:
        return await _naming(
            request,
            service,
            account,
            name,
            reference,
            refused="Give the account a name.",
        )
    await update_account(
        account_id,
        AccountUpdate(name=label, reference=reference.strip()),
        service=service,
        owner_user_id=owner_user_id,
    )
    # Blank leaves the stored number alone; it derives the mask.
    await set_number(service.db, account_id, account_number)
    # Whose money, said again. Blank hands it back to the household: a
    # field that can be set and not cleared is a mistake nobody can take
    # back.
    await subjects.held_by(
        service,
        account_id,
        await party_or_new(service.db, whose, new_whose, owner_user_id=owner_user_id),
        owner_user_id,
    )
    if routing_number.strip() and not await _routed(service, account, routing_number):
        return await _naming(
            request,
            service,
            account,
            name,
            reference,
            routing_number,
            "That is not a routing number; check for a transposed pair.",
        )
    await service.db.commit()
    return dialog_done(
        where_from(request, f"{SECTION.path}/{account_id}"), f"Renamed to {label}"
    )
