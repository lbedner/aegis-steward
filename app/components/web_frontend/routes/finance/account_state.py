"""Close or reopen, hide or show an account, from its Manage menu (#315).

Closed is the bank's word: the account ended, its history stays in the
register and in past months, and from the day it closed it leaves net
worth, the totals and every picker. Hidden is ours: out of every list
and total, history and all. Each opens a dialog that says which, and
posts back to itself.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, dialog_done, or_404
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date

SECTION = section("accounts")
router = APIRouter(prefix=SECTION.path)

# step -> (the button, what it does, the change it makes, the toast).
STEPS: dict[str, tuple[str, str, dict[str, bool], str]] = {
    "close": (
        "Close",
        "Its history stays in the register and in past months. From the day "
        "it closed it leaves net worth, the totals and the account pickers.",
        {"is_closed": True},
        "Closed",
    ),
    "reopen": (
        "Reopen",
        "It counts in net worth and the pickers again, all of its history included.",
        {"is_closed": False},
        "Reopened",
    ),
    "hide": (
        "Hide",
        "It leaves every list and total, its whole history with it. An "
        "account the bank closed is Close instead: its past still counts.",
        {"is_hidden": True},
        "Hid",
    ),
    "show": (
        "Show",
        "It is back in the lists and the totals.",
        {"is_hidden": False},
        "Showing",
    ),
}


def _routes(step: str) -> None:
    """GET (the dialog) and POST (the change) for one step: named paths,
    never ``/{id}/{step}``, which would answer for every account route."""
    title, explains, change, done = STEPS[step]

    async def form(
        request: Request,
        account_id: int,
        service: FinanceService = Depends(get_finance_service),
        owner_user_id: int | None = Depends(get_owner_user_id),
    ) -> Response:
        account = await service.get_account(account_id, owner_user_id=owner_user_id)
        or_404(account)
        return dialog(
            request,
            "partials/accounts/state.html",
            account=account,
            step=step,
            title=title,
            explains=explains,
            closed_on=current_date(),
        )

    async def apply(
        account_id: int,
        closed_on: Annotated[date | None, Form()] = None,
        service: FinanceService = Depends(get_finance_service),
        owner_user_id: int | None = Depends(get_owner_user_id),
    ) -> Response:
        # Closed is a fact, not a plan: a day ahead reads as today.
        if closed_on is not None:
            closed_on = min(closed_on, current_date())
        account = await service.update_account(
            account_id, owner_user_id=owner_user_id, closed_on=closed_on, **change
        )
        or_404(account)
        assert account is not None
        await service.db.commit()
        return dialog_done(f"{SECTION.path}/{account_id}", f"{done} {account.name}")

    path = f"/{{account_id:int}}/{step}"
    router.add_api_route(path, form, methods=["GET"], include_in_schema=False)
    router.add_api_route(path, apply, methods=["POST"], include_in_schema=False)


for _step in STEPS:
    _routes(_step)
