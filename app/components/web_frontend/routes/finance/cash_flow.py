"""Cash flow (#344): income and spending by category over a range, what
was kept and the savings rate, and the years side by side.

The figures and the years are one read of the rows the Overview's income
vs spending bars count; the two tables are the spending breakdown and its
mirror for money in. The filter is the Overview's: the range chips, a
from-to range, the accounts.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.accounts import list_accounts
from app.components.backend.api.finance.categories import (
    income_by_category,
    spending_by_category,
)
from app.components.backend.api.finance.overview import cash_flow
from app.components.web_frontend import ranges
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import render
from app.components.web_frontend.routes.finance.overview import (
    DEFAULT_DAYS,
    MAX_DAYS,
    RANGES,
    ranked,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date

SECTION = section("cash_flow")
router = APIRouter()


@router.get(SECTION.path, include_in_schema=False)
async def page(
    request: Request,
    picked: ranges.PickedRange,
    days: int = Query(default=DEFAULT_DAYS, ge=1),
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    start, end = picked
    window = ranges.horizon(days, MAX_DAYS, start)
    scope = {
        "account_ids": account_ids,
        "service": service,
        "owner_user_id": owner_user_id,
    }
    flow = await cash_flow(
        start=current_date() - timedelta(days=window - 1), end=end, **scope
    )
    spending = await spending_by_category(days=window, end=end, **scope)
    income = await income_by_category(days=window, end=end, **scope)
    accounts = await list_accounts(
        include_hidden=False,
        page=1,
        page_size=200,
        service=service,
        owner_user_id=owner_user_id,
    )
    response = render(
        request,
        "pages/cash_flow.html",
        {
            "section": SECTION,
            "days": days,
            "ranges": RANGES,
            "dates": (start, end),
            "accounts": accounts.items,
            "selected_ids": account_ids or [],
            "flow": flow,
            "income_rows": ranked(income, "category", "amount"),
            "spending_rows": ranked(spending, "category", "amount", tone="accent"),
        },
    )
    return ranges.remember(response, start, end)
