"""Merging a duplicate into the account you are on (#309).

A bank linked before it could attach made a copy beside the account an
export fed: the same money twice. Pick the copy, Check what folding it in
would do (``merging.plan``), then Merge (``merging.merge``): the account
you are on stays, with the copy's rows, its bank link, and each charge
both brought once.

Its own module because the Manage menu's is at its budget.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from app.components.web_frontend.filters import as_options
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, dialog_done, or_404
from app.components.web_frontend.routes.finance.account_manage import _account
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger import merging
from app.services.finance.models import FinanceAccount
from app.services.finance.service import FinanceService

SECTION = section("accounts")
router = APIRouter(prefix=SECTION.path)


async def _merge_dialog(
    request: Request,
    service: FinanceService,
    account: FinanceAccount,
    *,
    other: FinanceAccount | None = None,
    plan: merging.Plan | None = None,
) -> Response:
    """The pick - the bank copies that could fold in here - and once
    checked, what merging would do, or why it cannot."""
    candidates = await service.merge_candidates(account)
    refusal = plan.refusal if plan else None
    return dialog(
        request,
        "partials/accounts/merge.html",
        422 if refusal else 200,
        account=account,
        options=as_options([(str(a.id), a.name) for a in candidates]),
        other=other,
        plan=plan,
        errors=[refusal] if refusal else [],
    )


@router.get("/{account_id:int}/merge", include_in_schema=False)
async def merge_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return await _merge_dialog(request, service, account)


@router.post("/{account_id:int}/merge", include_in_schema=False)
async def merge(
    request: Request,
    account_id: int,
    other: Annotated[str, Form()] = "",
    preview: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """``preview`` re-renders the dialog with what a merge would do;
    without it the copy folds in and the account reopens."""
    account = await _account(service, account_id, owner_user_id)
    if not other.isdigit():  # the select's "Choose…"
        return await _merge_dialog(
            request, service, account, plan=merging.Plan(refusal="Choose the copy.")
        )
    copy = await _account(service, int(other), owner_user_id)
    plan = or_404(
        await service.merge_accounts(
            account_id, int(other), owner_user_id=owner_user_id, preview=bool(preview)
        )
    )
    if preview or plan.refusal:
        return await _merge_dialog(request, service, account, other=copy, plan=plan)
    await service.db.commit()
    return dialog_done(
        f"{SECTION.path}/{account_id}",
        f"Merged {copy.name}: {plan.moving} rows moved, "
        f"{plan.same_charges} charges kept once.",
    )
