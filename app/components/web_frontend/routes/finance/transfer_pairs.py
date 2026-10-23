"""A transfer's two halves, and a person's answer to a pairing (#317).

A paired row's mark opens both halves, where the pair can be broken; a
near miss offered in Attention is confirmed or turned down where it was
read.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.insights import dismiss_insight
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    where_from,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import (
    FinanceService,
    account_names,
    break_pair,
    confirm_pair,
    decline_offer,
    detection_queries,
    transactions_by_ids,
)

router = APIRouter()

# verb -> (the answer, what it says it did).
ANSWERS = {
    "confirm": (confirm_pair, "Paired: both sides leave spending"),
    "decline": (decline_offer, "Not a transfer: both sides still count"),
    "break": (break_pair, "Not a transfer: both sides count again"),
}


@router.get("/transactions/{transaction_id:int}/transfer", include_in_schema=False)
async def halves(
    request: Request,
    transaction_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Both halves of the transfer this row is one of."""
    transfer = or_404(
        await detection_queries.transfer_for_leg(
            service.db, transaction_id, owner_user_id
        )
    )
    ids = [i for i in (transfer.from_transaction_id, transfer.to_transaction_id) if i]
    legs = await transactions_by_ids(service.db, ids)
    names = await account_names(service.db, [leg.account_id for leg in legs.values()])
    return dialog(
        request,
        "partials/transactions/transfer.html",
        transaction_id=transaction_id,
        legs=[legs[i] for i in ids if i in legs],
        names=names,
    )


@router.post(
    "/transactions/{transaction_id:int}/transfer/{verb}", include_in_schema=False
)
async def answer(
    request: Request,
    transaction_id: int,
    verb: str,
    insight: Annotated[int | None, Query()] = None,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Confirm or turn down an offer, or break a pair. From an Attention
    item (``insight``), the item it answered goes with it."""
    move, said = or_404(ANSWERS.get(verb))
    or_404(await move(service.db, transaction_id, owner_user_id=owner_user_id))
    if insight is not None:
        from app.components.web_frontend.routes.finance.review import _resolved

        await dismiss_insight(insight, service=service, owner_user_id=owner_user_id)
        return await _resolved(request, service, owner_user_id, "attention", said)
    await service.db.commit()
    return dialog_done(where_from(request, "/accounts/all"), said)
