"""What an investment account HOLDS.

Its own module because that is a different question from the ones
``account_manage`` answers - what an account is called, what it is
worth, what it costs, what secures it - and because a file already past
its size budget cannot take another field until something leaves.

Positions arrive as a paste, one per line, and land all-or-nothing.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from app.components.web_frontend.filters import positions_from_text
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, with_toast
from app.components.web_frontend.routes.finance.account_manage import _account
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.models import FinanceAccount
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date

SECTION = section("accounts")
router = APIRouter(prefix=SECTION.path)


async def _positions_dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account: FinanceAccount,
    errors: list[str],
    status_code: int = 200,
    positions: str = "",
    as_of_date: str = "",
) -> Response:
    from app.components.backend.api.finance.investments import list_account_holdings

    held = await list_account_holdings(
        account.id, service=service, owner_user_id=owner_user_id
    )
    return dialog(
        request,
        "partials/accounts/positions.html",
        status_code,
        account=account,
        errors=errors,
        positions=positions,
        as_of_date=as_of_date or current_date().isoformat(),
        holdings=[
            {
                "ticker": h.ticker,
                "quantity": f"{h.quantity:g}",
                "value": h.market_value,
            }
            for h in held.items
        ],
    )


@router.get("/{account_id:int}/positions", include_in_schema=False)
async def positions_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return await _positions_dialog(request, service, owner_user_id, account, [])


@router.post("/{account_id:int}/positions", include_in_schema=False)
async def positions_save(
    request: Request,
    account_id: int,
    positions: Annotated[str, Form()] = "",
    as_of_date: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Pasted positions, all-or-nothing.

    A paste of twenty rows where two are malformed files NOTHING and says
    which two: filing eighteen leaves the account wrong in a way that
    looks right, and the reader has no way to tell which two are missing.
    """
    from app.components.backend.api.finance.investments import upsert_holding
    from app.services.finance.schemas.investments import HoldingCreate

    account = await _account(service, account_id, owner_user_id)
    rows, errors = positions_from_text(positions)
    if not errors and not rows:
        errors = ["Nothing to save - one position per line."]
    try:
        as_of = date.fromisoformat(as_of_date) if as_of_date else current_date()
    except ValueError:
        errors.append(f"{as_of_date!r} is not a date")
        as_of = current_date()
    if errors:
        return await _positions_dialog(
            request,
            service,
            owner_user_id,
            account,
            errors,
            422,
            positions=positions,
            as_of_date=as_of_date,
        )
    for row in rows:
        await upsert_holding(
            account.id,
            HoldingCreate(
                ticker=row["ticker"],
                quantity=row["quantity"],
                price=row["price"],
                as_of_date=as_of,
            ),
            service=service,
            owner_user_id=owner_user_id,
        )
    await service.db.commit()
    response = await _positions_dialog(request, service, owner_user_id, account, [])
    return with_toast(
        response, f"Saved {len(rows)} position{'s' if len(rows) != 1 else ''}"
    )
