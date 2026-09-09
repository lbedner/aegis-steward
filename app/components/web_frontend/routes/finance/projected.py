"""Projected: the cash forecast, rendered.

One call to the service's projection; the page draws it twice, as a
daily balance line (chart island) with the overdue occurrences marked,
and as the ledger of scheduled items under it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from app.components.web_frontend import ranges
from app.components.web_frontend.filters import short_date
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import render
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger.merchant_icon import icon_urls_for_payees
from app.services.finance.schemas import ProjectionPoint, ProjectionResponse
from app.services.finance.service import FinanceService

SECTION = section("projected")
router = APIRouter()

RANGES = ranges.WINDOWS
DEFAULT_DAYS = 90
# How far the forecast will look; also what "All" asks for.
MAX_DAYS = 730
COLUMNS = [
    {"key": "when", "label": "Date", "kind": "status"},
    {"key": "name", "label": "Name", "kind": "avatar"},
    {"key": "category", "label": "Category"},
    {"key": "account", "label": "Account"},
    {
        "key": "amount",
        "label": "Amount",
        "kind": "money",
        "align": "right",
        "signed": True,
    },
    {
        "key": "balance",
        "label": "Balance",
        "kind": "money",
        "align": "right",
        "toned": True,
    },
]


def balance_chart(projection: ProjectionResponse) -> dict[str, Any] | None:
    """A daily-resolution walk (a quiet month reads as a flat stretch) plus
    a marker series: the balance on each day an overdue bill was applied.
    No events means nothing to draw."""
    if not projection.points:
        return None
    by_day: dict[str, list[ProjectionPoint]] = {}
    for point in projection.points:
        by_day.setdefault(point.date.isoformat(), []).append(point)
    labels: list[str] = []  # short dates; the chart reads them as text
    values: list[float] = []
    overdue: list[float | None] = []
    balance = projection.start_balance
    for offset in range(projection.horizon_days + 1):
        key = (projection.as_of + timedelta(days=offset)).isoformat()
        events = by_day.get(key, [])
        if events:
            balance = events[-1].balance
        labels.append(short_date(key))
        values.append(balance / 100)
        overdue.append(balance / 100 if any(e.due_date for e in events) else None)
    return {
        "labels": labels,
        "series": [
            {"label": "Balance", "values": values},
            {"label": "Overdue", "values": overdue, "points": True},
        ],
    }


def ledger_rows(
    points: list[ProjectionPoint], icons: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """An overdue occurrence lands on today so the balance is right, but
    the row shows the day it was owed, in the overdue tone. ``icons``
    maps a name to its brand mark URL."""
    return [
        {
            "when": {
                "label": short_date(p.due_date or p.date),
                "tone": "warn" if p.due_date else None,
            },
            "name": p.name,
            "icon_url": (icons or {}).get(p.name),
            "category": p.category,
            "account": p.account,
            "amount": p.amount,
            "balance": p.balance,
        }
        for p in points
    ]


@router.get(SECTION.path, include_in_schema=False)
async def page(
    request: Request,
    days: int = Query(default=DEFAULT_DAYS, ge=1),
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    projection = await service.project_balances(
        owner_user_id=owner_user_id,
        days=ranges.horizon(days, MAX_DAYS),
        account_ids=account_ids or None,
    )
    accounts, _total = await service.list_accounts(
        owner_user_id=owner_user_id, page_size=500
    )
    icons = await icon_urls_for_payees(
        service.db, [p.name for p in projection.points], owner_user_id=owner_user_id
    )
    return render(
        request,
        "pages/projected.html",
        {
            "section": SECTION,
            "days": days,
            "ranges": RANGES,
            "selected_ids": account_ids or [],
            "accounts": accounts,
            "projection": projection,
            "chart": balance_chart(projection),
            "rows": ledger_rows(projection.points, icons),
            "columns": COLUMNS,
        },
    )
