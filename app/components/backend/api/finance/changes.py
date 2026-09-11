"""The propose/approve queue's HTTP surface (FW-05).

Approval is the only execution path, and it lives here - behind the
app user - never in a tool. A proposal that fails execution stays
pending with its error in the payload the card renders.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.formatting import payee_label
from app.core.log import logger
from app.services.finance.deps import (
    get_finance_service,
    get_owner_user_id,
)
from app.services.finance.domains import writes
from app.services.finance.models import FinancePendingChange
from app.services.finance.schemas import (
    BatchResolveRequest,
    BatchResolveResponse,
    ChangeProposal,
    PendingChangeListResponse,
    PendingChangeResponse,
)
from app.services.finance.service import FinanceService

router = APIRouter()


async def _marks(
    service: FinanceService, rows: list[FinancePendingChange]
) -> dict[int, dict[str, str | None]]:
    """The brand each proposal wears, by change id.

    A proposal is about a transaction, so it takes that row's mark — the
    same one the register gives it. Most payments a proposal concerns
    have no payee of their own, though, because that is usually why they
    are unmatched; when the change also names a bill, the row borrows the
    bill's brand instead of showing an initial taken off a raw bank
    descriptor. The proposal is asserting the two are the same thing.

    Both hydrations are batched. Icons are one lookup per batch and never
    per row, so neither can be folded into a loop over changes.
    """
    from app.components.backend.api.finance.recurring import hydrate_streams
    from app.components.backend.api.finance.register import hydrate_transactions

    def _key(row: FinancePendingChange, name: str) -> int | None:
        value = (row.payload or {}).get(name)
        return value if isinstance(value, int) else None

    txn_ids = {i for i in (_key(r, "transaction_id") for r in rows) if i}
    stream_ids = {i for i in (_key(r, "stream_id") for r in rows) if i}

    by_txn: dict[int, dict[str, str | None]] = {}
    curated: set[int] = set()
    if txn_ids:
        found = await service.transactions_by_ids(list(txn_ids))
        for item in await hydrate_transactions(service, list(found.values())):
            if item.id is None:
                continue
            by_txn[item.id] = {
                "payee": payee_label(item.merchant, item.merchant_name, item.name),
                "icon_url": item.icon_url,
                "category": item.category,
            }
            if item.merchant:
                curated.add(item.id)

    by_stream: dict[int, dict[str, str | None]] = {}
    if stream_ids:
        streams = [
            s for s in await service.streams_by_ids(list(stream_ids)) if s is not None
        ]
        for item in await hydrate_streams(service, streams, None):
            if item.id is not None:
                by_stream[item.id] = {
                    "payee": item.name,
                    "icon_url": item.icon_url,
                    "category": item.category_name,
                }

    marks: dict[int, dict[str, str | None]] = {}
    for row in rows:
        if row.id is None:
            continue
        txn_id, stream_id = _key(row, "transaction_id"), _key(row, "stream_id")
        own = by_txn.get(txn_id) if txn_id else None
        borrowed = by_stream.get(stream_id) if stream_id else None
        # The payment's own payee wins when it has one; otherwise the bill's.
        mark = own if txn_id in curated else (borrowed or own)
        if mark:
            marks[row.id] = mark
    return marks


async def _to_response(
    service: FinanceService,
    row: FinancePendingChange,
    marks: dict[int, dict[str, str | None]] | None = None,
) -> PendingChangeResponse:
    executor = writes.executor_for(row.change_type)
    display = await service.describe_pending_change(row)
    if marks is None:
        marks = await _marks(service, [row])
    return PendingChangeResponse.from_row(
        row, title=executor.title, display=display, mark=marks.get(row.id or 0)
    )


async def _to_responses(
    service: FinanceService, rows: list[FinancePendingChange]
) -> list[PendingChangeResponse]:
    """A list of proposals, with their marks resolved in one pass."""
    marks = await _marks(service, rows)
    return [await _to_response(service, row, marks) for row in rows]


@router.post("/changes", response_model=PendingChangeResponse)
async def propose_change(
    body: ChangeProposal,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> PendingChangeResponse:
    """Record a proposal. Nothing in the ledger moves here."""
    try:
        row = await service.propose_change(
            body.change_type,
            body.payload,
            owner_user_id=owner_user_id,
            proposed_by_agent=body.proposed_by_agent,
            conversation_id=body.conversation_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from None
    response = await _to_response(service, row)
    await service.db.commit()
    return response


@router.get("/changes", response_model=PendingChangeListResponse)
async def list_changes(
    status_filter: str | None = Query("pending", alias="status"),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> PendingChangeListResponse:
    rows = await service.list_pending_changes(
        owner_user_id=owner_user_id, status=status_filter
    )
    items = await _to_responses(service, rows)
    return PendingChangeListResponse(items=items, total=len(items))


@router.get("/changes/{change_id}", response_model=PendingChangeResponse)
async def get_change(
    change_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> PendingChangeResponse:
    row = await service.get_pending_change(change_id, owner_user_id=owner_user_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Change not found"
        )
    return await _to_response(service, row)


@router.post("/changes/{change_id}/approve", response_model=PendingChangeResponse)
async def approve_change(
    change_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> PendingChangeResponse:
    """Execute the stored mutation - the one door into the ledger."""
    try:
        row = await service.approve_change(change_id, owner_user_id=owner_user_id)
    except ValueError as e:
        await service.db.commit()  # a recorded execution error is audit
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from None
    except Exception:
        # Same audit rule for a NON-domain crash - the row's recorded
        # error must survive, or the card shows a pending change with
        # no explanation - but internals never reach the client.
        await service.db.commit()
        logger.exception(f"Executing pending change {change_id} failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The change could not be executed.",
        ) from None
    response = await _to_response(service, row)
    await service.db.commit()
    return response


@router.post("/changes/{change_id}/reject", response_model=PendingChangeResponse)
async def reject_change(
    change_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> PendingChangeResponse:
    try:
        row = await service.reject_change(change_id, owner_user_id=owner_user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from None
    response = await _to_response(service, row)
    await service.db.commit()
    return response


@router.get("/changes/batch/{batch_id}", response_model=PendingChangeListResponse)
async def get_batch(
    batch_id: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> PendingChangeListResponse:
    """Every row of one batch, whatever its status - the batch card's
    refresh source."""
    rows = await writes.batch_rows(service.db, batch_id, owner_user_id=owner_user_id)
    items = await _to_responses(service, rows)
    return PendingChangeListResponse(items=items, total=len(items))


@router.post("/changes/batch/{batch_id}/approve", response_model=BatchResolveResponse)
async def approve_batch(
    batch_id: str,
    body: BatchResolveRequest,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> BatchResolveResponse:
    """Approve the batch's pending rows, rejecting any vetoed ids."""
    summary = await service.approve_batch(
        batch_id, owner_user_id=owner_user_id, exclude_ids=body.exclude_ids
    )
    await service.db.commit()
    return BatchResolveResponse(**summary)


@router.post("/changes/batch/{batch_id}/reject", response_model=BatchResolveResponse)
async def reject_batch(
    batch_id: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> BatchResolveResponse:
    summary = await service.reject_batch(batch_id, owner_user_id=owner_user_id)
    await service.db.commit()
    return BatchResolveResponse(**summary)
