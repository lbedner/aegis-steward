"""Marking rows reviewed (#322).

``is_reviewed`` was set by categorizing or naming a payee and never read.
Now each row shows it, the register filters on it, and it is set or
cleared directly - one row from its menu, or a selection - through the
one row-action contract: every touched row comes back out of band.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from app.components.web_frontend.rendering import with_toast
from app.components.web_frontend.routes.finance.transactions import _txns, rows_response
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import FinanceService

router = APIRouter(prefix="/transactions")


@router.post("/reviewed", include_in_schema=False)
async def mark_reviewed(
    request: Request,
    transaction_ids: Annotated[list[int], Form()] = [],  # noqa: B006
    reviewed: Annotated[bool, Form()] = True,
    show_account: Annotated[bool, Form()] = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Mark the rows reviewed, or (``reviewed`` false) not reviewed."""
    txns = await _txns(service, transaction_ids, owner_user_id)
    for txn in txns:
        txn.is_reviewed = reviewed
        service.db.add(txn)
    await service.db.commit()
    response = await rows_response(request, service, txns, owner_user_id, show_account)
    said = "reviewed" if reviewed else "not reviewed"
    return with_toast(response, f"Marked {len(txns)} {said}.")
