"""Charges that will be paid back (#327): the dialog on a register row,
and Review's list of the ones still open.

The row's menu opens one dialog for the whole life of a reimbursable:
mark it (how much, from whom), link what pays it back, let it go. It
answers with itself, so every step redraws only the dialog.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.web_frontend.filters import as_options, money, short_date
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog,
    render,
    with_toast,
)
from app.components.web_frontend.routes.finance.review import SECTION, nav_context
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger import reimbursements
from app.services.finance.service import FinanceService
from app.services.finance.utils import money_to_cents

router = APIRouter()

OPEN_COLUMNS = [
    {"key": "date", "label": "Date", "kind": "date"},
    {"key": "name", "label": "Charge"},
    {"key": "source", "label": "From"},
    {"key": "expected", "label": "Expected", "kind": "money", "align": "right"},
    {"key": "outstanding", "label": "Still owed", "kind": "money", "align": "right"},
]


async def _dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    transaction_id: int,
    errors: list[str] | None = None,
    found: reimbursements.Reimbursable | None = None,
) -> Response:
    """The dialog, drawn from ``found`` when a write already holds it."""
    if found is None:
        found = await reimbursements.reimbursable_for(
            service.db, owner_user_id, transaction_id
        )
    try:
        charge = (
            found.charge
            if found
            else await reimbursements.own_row(service.db, owner_user_id, transaction_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404) from exc
    return dialog(
        request,
        "partials/transactions/reimbursable.html",
        422 if errors else 200,
        charge=charge,
        found=found,
        candidates=as_options(
            [
                (
                    str(t.id),
                    f"{short_date(t.date_)} · {t.merchant_name or t.name} · {money(t.amount)}",
                )
                for t in (
                    await reimbursements.repayment_candidates(
                        service.db, owner_user_id, found
                    )
                    if found
                    else []
                )
            ]
        ),
        errors=errors or [],
    )


@router.get("/transactions/{transaction_id:int}/reimbursable", include_in_schema=False)
async def form(
    request: Request,
    transaction_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return await _dialog(request, service, owner_user_id, transaction_id)


@router.post("/transactions/{transaction_id:int}/reimbursable", include_in_schema=False)
async def mark(
    request: Request,
    transaction_id: int,
    expected: Annotated[str, Form()] = "",
    from_name: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Mark the charge reimbursable, or restate what is expected."""
    cents = money_to_cents(expected) if expected.strip() else None
    try:
        found = await reimbursements.mark(
            service.db,
            owner_user_id,
            transaction_id,
            expected_amount=abs(cents) if cents is not None else None,
            from_name=from_name,
            note=note,
        )
    except ValueError as exc:
        return await _dialog(
            request, service, owner_user_id, transaction_id, [str(exc)]
        )
    await service.db.commit()
    return await _dialog(request, service, owner_user_id, transaction_id, found=found)


@router.post(
    "/transactions/{transaction_id:int}/reimbursable/repayments",
    include_in_schema=False,
)
async def link(
    request: Request,
    transaction_id: int,
    repayment_id: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """This money paid it back, or some of it."""
    if not repayment_id.isdigit():
        return await _dialog(
            request, service, owner_user_id, transaction_id, ["Choose the repayment."]
        )
    try:
        found = await reimbursements.link_repayment(
            service.db, owner_user_id, transaction_id, int(repayment_id)
        )
    except ValueError as exc:
        return await _dialog(
            request, service, owner_user_id, transaction_id, [str(exc)]
        )
    await service.db.commit()
    return await _dialog(request, service, owner_user_id, transaction_id, found=found)


@router.delete(
    "/transactions/{transaction_id:int}/reimbursable/repayments/{repayment_id:int}",
    include_in_schema=False,
)
async def unlink(
    request: Request,
    transaction_id: int,
    repayment_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    found = await reimbursements.unlink_repayment(
        service.db, owner_user_id, transaction_id, repayment_id
    )
    await service.db.commit()
    return await _dialog(request, service, owner_user_id, transaction_id, found=found)


@router.delete(
    "/transactions/{transaction_id:int}/reimbursable", include_in_schema=False
)
async def unmark(
    transaction_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Not reimbursable after all."""
    await reimbursements.unmark(service.db, owner_user_id, transaction_id)
    await service.db.commit()
    return close_dialog(
        with_toast(Response(status_code=200), "No longer reimbursable.")
    )


@router.get(SECTION.path + "/reimbursable", include_in_schema=False)
async def open_list(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """What is still owed back, oldest first."""
    rows = await reimbursements.reimbursables(service.db, owner_user_id, open_only=True)
    return render(
        request,
        "pages/review/reimbursable.html",
        {
            "section": SECTION,
            **await nav_context(service, owner_user_id, "reimbursable"),
            "columns": OPEN_COLUMNS,
            "rows": [
                {
                    "id": r.charge.id,
                    "date": r.charge.date_,
                    "name": r.charge.merchant_name or r.charge.name,
                    "source": r.reimbursement.from_name or "",
                    "expected": r.reimbursement.expected_amount,
                    "outstanding": r.outstanding,
                }
                for r in rows
            ],
        },
    )
