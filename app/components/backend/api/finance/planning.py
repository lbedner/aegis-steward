"""Envelopes - the set-aside accounts without a finish line.

One sub-router of the finance API (see ``router.py``, the aggregator).
Goals, their close cousin, live in ``goals.py``.
"""

from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.components.backend.api.finance.base import _NOT_FOUND
from app.services.finance.deps import (
    get_finance_service,
    get_owner_user_id,
)
from app.services.finance.schemas import (
    EnvelopeCreate,
    EnvelopeListResponse,
    EnvelopeMove,
    EnvelopeResponse,
    EnvelopeUpdate,
)
from app.services.finance.service import FinanceService

router = APIRouter()


# -- Envelopes ------------------------------------------------------------
#
# Virtual sub-accounts (an allowance the kid draws down): hidden manual
# accounts whose balance and history ride valuations. Both directions,
# no target - the goals design minus the finish line.


async def envelope_response(db: Any, account: Any) -> EnvelopeResponse:
    from app.services.finance.domains.planning.envelope_tags import tag_name
    from app.services.finance.domains.planning.envelopes import envelope_metadata

    meta = envelope_metadata(account.metadata_)
    assert meta is not None  # callers only pass envelope accounts
    return EnvelopeResponse(
        account_id=account.id,
        name=account.name,
        balance=account.current_balance or 0,
        monthly_credit=meta.monthly_credit,
        auto_credit=meta.auto_credit,
        cadence=meta.cadence,
        tag=await tag_name(db, meta),
        tag_since=meta.tag_since,
    )


@router.get("/envelopes", response_model=EnvelopeListResponse)
async def list_envelopes(
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> EnvelopeListResponse:
    accounts = await service.list_envelopes(owner_user_id=owner_user_id)
    items = [await envelope_response(service.db, a) for a in accounts]
    return EnvelopeListResponse(items=items, total=len(items))


@router.post(
    "/envelopes", response_model=EnvelopeResponse, status_code=status.HTTP_201_CREATED
)
async def create_envelope(
    body: EnvelopeCreate,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> EnvelopeResponse:
    account = await service.create_envelope(
        owner_user_id=owner_user_id,
        name=body.name,
        monthly_credit=body.monthly_credit,
        cadence=body.cadence,
        starting_balance=body.starting_balance,
    )
    return await envelope_response(service.db, account)


@router.patch("/envelopes/{account_id}", response_model=EnvelopeResponse)
async def update_envelope(
    account_id: int,
    body: EnvelopeUpdate,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> EnvelopeResponse:
    account = await service.update_envelope(
        account_id,
        owner_user_id=owner_user_id,
        monthly_credit=body.monthly_credit,
        auto_credit=body.auto_credit,
        cadence=body.cadence,
        tag=body.tag,
        tag_since=body.tag_since,
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return await envelope_response(service.db, account)


@router.delete("/envelopes/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_envelope(
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> None:
    from app.services.finance.domains.planning.envelopes import envelope_metadata

    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    if account is None or envelope_metadata(account.metadata_) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    await service.soft_delete_account(account_id, owner_user_id=owner_user_id)


@router.post("/envelopes/{account_id}/credit", response_model=EnvelopeResponse)
async def credit_envelope(
    account_id: int,
    body: EnvelopeMove,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> EnvelopeResponse:
    try:
        account = await service.credit_envelope(
            account_id,
            amount=body.amount,
            owner_user_id=owner_user_id,
            when=body.when,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return await envelope_response(service.db, account)


@router.post("/envelopes/{account_id}/spend", response_model=EnvelopeResponse)
async def spend_from_envelope(
    account_id: int,
    body: EnvelopeMove,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> EnvelopeResponse:
    try:
        account = await service.spend_from_envelope(
            account_id,
            amount=body.amount,
            owner_user_id=owner_user_id,
            when=body.when,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return await envelope_response(service.db, account)
