"""The landing figures: health, overview, net worth, cashflow.

One sub-router of the finance API (see ``router.py``, the aggregator).
"""

from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    Query,
)

from app.components.backend.api.finance.accounts import list_accounts
from app.components.backend.api.finance.categories import (
    spending_by_category,
    spending_moves,
)
from app.components.backend.api.finance.payees import top_payees
from app.components.backend.api.finance.recurring import recurring_projection
from app.components.backend.api.finance.register import (
    list_transactions,
    uncategorized_transactions,
)
from app.services.finance.deps import get_finance_service
from app.services.finance.schemas import (
    CashFlowResponse,
    CashflowResponse,
    FinanceHealth,
    FinanceOverviewResponse,
    NetWorthByType,
    NetWorthPoint,
    SpendingPace,
)
from app.services.finance.service import FinanceService
from app.services.shared.deps import get_owner_user_id

router = APIRouter()


@router.get("/health", response_model=FinanceHealth)
async def finance_health(
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> FinanceHealth:
    """Liveness + a quick summary (account/connection counts, overall status),
    scoped to the caller (aggregate across all accounts in standalone mode)."""
    return await service.health(owner_user_id=owner_user_id)


@router.get("/overview", response_model=FinanceOverviewResponse)
async def finance_overview(
    days: int = Query(default=180, ge=1, le=3650),
    months: int = Query(default=6, ge=1, le=36),
    projection_days: int = Query(default=30, ge=1, le=730),
    preview_limit: int = Query(default=7, ge=1, le=50),
    account_ids: list[int] | None = Query(default=None),
    end: date | None = None,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> FinanceOverviewResponse:
    """The Overview surface in ONE round trip.

    Composes the eight granular endpoints the modal used to call
    individually - same handlers, same shapes - so opening the tab costs
    one request and one DB session instead of eight of each. The
    granular endpoints remain for targeted refreshes; this is the
    surface's front door (house rule: one surface, one composite).
    ``account_ids`` scopes the windowed aggregates (net worth, cashflow,
    spending), exactly as the surface applied it before, and ``end`` stops
    them at the last day of a from-to range (#342).
    """
    # The bars and the pace count the same rows: one read for both.
    months_rows, pace = await service.overview_flows(
        owner_user_id=owner_user_id, months=months, end=end, account_ids=account_ids
    )
    return FinanceOverviewResponse(
        accounts=await list_accounts(
            include_hidden=False,
            page=1,
            page_size=200,
            service=service,
            owner_user_id=owner_user_id,
        ),
        net_worth=await net_worth_series(
            days=days,
            account_ids=account_ids,
            end=end,
            service=service,
            owner_user_id=owner_user_id,
        ),
        cashflow=CashflowResponse(items=months_rows, total=len(months_rows)),
        top_payees=await top_payees(
            days=days,
            limit=preview_limit,
            service=service,
            owner_user_id=owner_user_id,
        ),
        projection=await recurring_projection(
            days=projection_days,
            account_ids=None,
            service=service,
            owner_user_id=owner_user_id,
        ),
        recent_transactions=await list_transactions(
            account_id=None,
            account_ids=None,
            from_date=None,
            to_date=None,
            category_id=None,
            merchant_id=None,
            without_merchant=False,
            tag_id=None,
            q=None,
            include_transfers=False,
            page=1,
            page_size=preview_limit,
            service=service,
            owner_user_id=owner_user_id,
        ),
        uncategorized=await uncategorized_transactions(
            limit=preview_limit,
            q=None,
            from_date=None,
            account_ids=None,
            service=service,
            owner_user_id=owner_user_id,
        ),
        spending=await spending_by_category(
            days=days,
            account_ids=account_ids,
            end=end,
            service=service,
            owner_user_id=owner_user_id,
        ),
        category_moves=await spending_moves(
            limit=preview_limit, service=service, owner_user_id=owner_user_id
        ),
        pace=pace,
    )


@router.get("/net-worth", response_model=list[NetWorthPoint])
async def net_worth_series(
    days: int = 90,
    account_ids: list[int] | None = Query(default=None),
    without_house: bool = False,
    end: date | None = None,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> list[NetWorthPoint]:
    """Net-worth-over-time series, oldest first, straight off the snapshot
    table (materialized nightly by the scheduler job). ``account_ids``
    charts just those accounts, summed from the per-account snapshots;
    ``without_house`` leaves the property and its mortgage out."""
    rows = await service.get_net_worth_series(
        owner_user_id=owner_user_id,
        days=days,
        account_ids=account_ids,
        without_house=without_house,
        until=end,
    )
    return [NetWorthPoint.from_row(row) for row in rows]


@router.get("/net-worth/by-type", response_model=NetWorthByType)
async def net_worth_by_type(
    days: int = 90,
    account_ids: list[int] | None = Query(default=None),
    without_house: bool = False,
    end: date | None = None,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> NetWorthByType:
    """Net worth by component (#343): each account group's balance per day,
    debts below zero, summing to the net."""
    return await service.net_worth_by_type(
        owner_user_id=owner_user_id,
        days=days,
        account_ids=account_ids,
        without_house=without_house,
        until=end,
    )


@router.get("/spending/pace", response_model=SpendingPace)
async def spending_pace(
    months: int = Query(default=12, ge=1, le=36),
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> SpendingPace:
    """This month's cumulative spending by day beside the ``months`` before
    it at the same day: average, median and last month (#305)."""
    return await service.spending_pace(
        owner_user_id=owner_user_id, months=months, account_ids=account_ids
    )


@router.get("/cash-flow", response_model=CashFlowResponse)
async def cash_flow(
    start: date,
    end: date | None = None,
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> CashFlowResponse:
    """Money in and out from ``start`` through ``end`` (default today), the
    savings rate, and each year's share (#344). Transfers are out, as in
    the monthly bars."""
    return await service.cash_flow(
        owner_user_id=owner_user_id, start=start, end=end, account_ids=account_ids
    )


@router.get("/cashflow", response_model=CashflowResponse)
async def monthly_cashflow(
    months: int = Query(default=6, ge=1, le=36),
    account_ids: list[int] | None = Query(default=None),
    end: date | None = None,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> CashflowResponse:
    """Income vs spend per calendar month, oldest first, transfers excluded.
    ``account_ids`` narrows the bars to those accounts; ``end`` makes the
    last month the one it falls in, counted through that day."""
    rows = await service.monthly_cashflow(
        owner_user_id=owner_user_id, months=months, account_ids=account_ids, today=end
    )
    return CashflowResponse(items=rows, total=len(rows))
