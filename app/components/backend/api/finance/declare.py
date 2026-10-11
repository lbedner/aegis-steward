"""The "Make recurring" path: preview and declare.

One sub-router of the finance API (see ``router.py``, the aggregator).
"""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.services.finance.deps import get_finance_service
from app.services.finance.domains.ledger.queries.accounts import account_names
from app.services.finance.schemas import (
    DeclareRecurring,
    RecurringPlanEntry,
    RecurringPlanMember,
    RecurringPlanResponse,
)
from app.services.finance.service import FinanceService
from app.services.shared.deps import get_owner_user_id

router = APIRouter()


@router.post(
    "/transactions/declare-recurring/preview", response_model=RecurringPlanResponse
)
async def preview_declare_recurring(
    body: DeclareRecurring,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> RecurringPlanResponse:
    """What "Make recurring" would do, without doing it.

    Worth a round trip because the write is not confined to the rows the
    user ticked: it sweeps in every sibling of the same payee and folds
    away whatever was already describing the bill. Both of those are
    surprises unless shown first, and the proposed name is only editable
    if there is somewhere to edit it.
    """
    from app.services.finance.domains.detection import plan_recurring

    plan = await plan_recurring(
        service.db,
        body.transaction_ids,
        owner_user_id=owner_user_id,
        exclude_transaction_ids=body.exclude_transaction_ids,
    )
    accounts = await account_names(service.db, [group.account_id for group in plan])
    items = [
        RecurringPlanEntry(
            key=group.key,
            name=group.name,
            account_id=group.account_id,
            account_name=accounts.get(group.account_id),
            direction=group.direction,
            frequency=group.frequency,
            average_amount=group.average_amount,
            last_amount=group.last_amount,
            first_date=str(group.first_date) if group.first_date else None,
            last_date=str(group.last_date) if group.last_date else None,
            next_expected_date=(
                str(group.next_expected_date) if group.next_expected_date else None
            ),
            amount_is_variable=group.variable,
            occurrence_count=group.occurrence_count,
            selected_count=group.selected_count,
            selected_amount=group.selected_amount,
            absorbs=group.absorbs,
            creates_new_bill=group.creates_new_bill,
            existing_bill_name=group.existing_bill_name,
            members=[
                RecurringPlanMember(
                    id=m.id,
                    date=str(m.date_),
                    name=m.merchant_name or m.name or m.original_description or "",
                    amount=m.amount,
                )
                for m in group.members
            ],
        )
        for group in plan
    ]
    return RecurringPlanResponse(
        items=items,
        total_transactions=sum(i.occurrence_count for i in items),
    )


@router.post("/transactions/declare-recurring")
async def declare_recurring_transactions(
    body: DeclareRecurring,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> dict[str, int]:
    """Turn the selected transactions into confirmed bills or income.

    Detection can only ever guess, and it declines in exactly the cases a
    user is most certain about: fewer than three occurrences so far, or a
    cadence that matches no canonical gap. This is the override - and it
    reconciles at the same time, folding whatever else already described
    the same bill into one stream (see ``declare_recurring``).
    """
    from app.services.finance.domains.detection import declare_recurring

    if not body.transaction_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one transaction id.",
        )
    result = await declare_recurring(
        service.db,
        body.transaction_ids,
        owner_user_id=owner_user_id,
        names=body.names,
        exclude_transaction_ids=body.exclude_transaction_ids,
        categories=body.categories,
        amounts=body.amounts,
        frequencies=body.frequencies,
    )
    await service.db.commit()
    return {
        "streams": result.streams,
        "transactions": result.transactions,
        "reconciled": result.reconciled,
    }
