"""Settings > Payees: every merchant the ledger knows, and cleaning one up.

A merchant arriving lowercase or under the wrong brand is renamed once,
on the payee, and every charge shows it; two payees for one merchant are
merged, the other's charges, bills and descriptors moving to the one kept
(#328). Its own module because Settings is at its budget.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.backend.api.finance.payees import (
    list_merchants,
    merchant_category_summary,
)
from app.components.web_frontend.filters import as_options
from app.components.web_frontend.nav import settings_nav as nav_context
from app.components.web_frontend.rendering import dialog, dialog_done, render
from app.components.web_frontend.routes.finance.settings import SECTION
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import FinanceService

router = APIRouter(prefix=SECTION.path)
PAYEES = f"{SECTION.path}/payees"

PAYEE_COLUMNS = [
    {"key": "name", "label": "Name", "kind": "avatar"},
    {
        "key": "transaction_count",
        "label": "Transactions",
        "kind": "int",
        "align": "right",
    },
    {
        "key": "total_amount",
        "label": "Total",
        "kind": "money",
        "align": "right",
        "toned": True,
    },
    {"key": "last_date", "label": "Last seen", "kind": "date"},
]


async def _payees(service: FinanceService, owner_user_id: int | None) -> list[Any]:
    listing = await list_merchants(
        account_ids=None, service=service, owner_user_id=owner_user_id
    )
    return listing.items


async def _payee(
    service: FinanceService, owner_user_id: int | None, merchant_id: int
) -> tuple[Any, list[Any]]:
    """One payee, and the rest - what it could be merged with. Names only:
    the directory's usage totals and logo fetches serve no dialog."""
    rows = await service.list_merchants(owner_user_id=owner_user_id)
    row = next((r for r in rows if r.id == merchant_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="No such payee.")
    return row, [r for r in rows if r.id != merchant_id]


@router.get("/payees", include_in_schema=False)
async def payees(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return render(
        request,
        "pages/settings/payees.html",
        {
            "section": SECTION,
            **nav_context("payees"),
            "rows": await _payees(service, owner_user_id),
            "columns": PAYEE_COLUMNS,
            "path": SECTION.path,
        },
    )


@router.get("/payees/{merchant_id:int}/categories", include_in_schema=False)
async def payee_categories(
    request: Request,
    merchant_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """How this payee's transactions are filed today: a payee arguing with
    itself shows up here as two rows."""
    # One name, not the whole directory: listing it also aggregates usage
    # and schedules icon fetches, which a single dialog has no use for.
    names = await service.merchant_names({merchant_id})
    if merchant_id not in names:
        raise HTTPException(status_code=404)
    summary = await merchant_category_summary(
        merchant_id, service=service, owner_user_id=owner_user_id
    )
    return dialog(
        request,
        "partials/settings/payee_categories.html",
        name=names[merchant_id],
        summary=summary,
    )


@router.get("/payees/{merchant_id:int}/edit", include_in_schema=False)
async def payee_form(
    request: Request,
    merchant_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    payee, others = await _payee(service, owner_user_id, merchant_id)
    return _edit_dialog(request, payee, others)


def _edit_dialog(
    request: Request, payee: Any, others: list[Any], errors: list[str] | None = None
) -> Response:
    return dialog(
        request,
        "partials/settings/payee_edit.html",
        422 if errors else 200,
        payee=payee,
        others=as_options([(str(r.id), r.name) for r in others]),
        errors=errors or [],
    )


@router.post("/payees/{merchant_id:int}/edit", include_in_schema=False)
async def payee_save(
    request: Request,
    merchant_id: int,
    name: Annotated[str, Form()] = "",
    website_url: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Rename a payee, and correct its website: every charge shows its name."""
    payee, others = await _payee(service, owner_user_id, merchant_id)
    if not name.strip():
        return _edit_dialog(request, payee, others, ["A payee needs a name."])
    await service.update_merchant(
        merchant_id, owner_user_id=owner_user_id, name=name, website_url=website_url
    )
    await service.db.commit()
    return dialog_done(PAYEES, f"Saved {name.strip()}.")


@router.post("/payees/{merchant_id:int}/merge", include_in_schema=False)
async def payee_merge(
    request: Request,
    merchant_id: int,
    other: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Fold another payee into this one: its charges, bills and the
    descriptors it was taught move here, and it goes."""
    payee, others = await _payee(service, owner_user_id, merchant_id)
    copy = next((r for r in others if str(r.id) == other), None)
    if copy is None:
        return _edit_dialog(request, payee, others, ["Choose the payee to merge in."])
    moved = await service.merge_merchants(
        [copy.id], merchant_id, owner_user_id=owner_user_id
    )
    await service.db.commit()
    return dialog_done(PAYEES, f"Merged {copy.name} into {payee.name}: {moved} moved.")
