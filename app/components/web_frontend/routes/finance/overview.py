"""The overview section: the composite overview, rendered.

One in-process call to the API's ``finance_overview`` (one DB session,
eight sub-reads) and a little presentation shaping, all pure functions
below so they are testable without a request.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.categories import spending_transactions
from app.components.backend.api.finance.overview import finance_overview
from app.components.web_frontend import ranges
from app.components.web_frontend.filters import money
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import render, templates
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger.accounts import effective_balance
from app.services.finance.domains.planning.recurring.forecast import upcoming_outflows
from app.services.finance.schemas import (
    AccountResponse,
    CashflowMonth,
    NetWorthPoint,
    ProjectionResponse,
    SpendingCategory,
)
from app.services.finance.service import FinanceService

SECTION = section("overview")
router = APIRouter()

RANGES = ranges.WINDOWS
# What "All" asks the composite for; also its cap.
MAX_DAYS = 3650
DEFAULT_DAYS = 90
PREVIEW = 7
# Named slices before the tail folds into "Other". Measured on a real
# ledger: 10 left "Other" at 16%, 15 gets it under 6% with every further
# category under 1% of spend (the Flet Overview's own tuning).
PIE_SLICES = 15
# Bars before months fold to quarters, then years.
MAX_CASHFLOW_BARS = 12

TXN_COLUMNS = [
    {"key": "date", "label": "Date", "kind": "date"},
    {"key": "name", "label": "Name"},
    {"key": "category", "label": "Category"},
    {"key": "amount", "label": "Amount", "kind": "money", "align": "right"},
]
PAYEE_COLUMNS = [
    {"key": "payee", "label": "Payee"},
    {"key": "amount", "label": "Spent", "kind": "money", "align": "right"},
    {"key": "transaction_count", "label": "Count", "kind": "int", "align": "right"},
]
BILL_COLUMNS = [
    {"key": "name", "label": "Bill"},
    {"key": "date", "label": "Due", "kind": "date"},
    {"key": "amount", "label": "Amount", "kind": "money", "align": "right"},
]


def totals(accounts: list[AccountResponse], selected: list[int]) -> dict[str, int]:
    """Assets, liabilities and net worth over the accounts in view, using
    the domain's own effective-balance rule (what the assistant reports)."""
    rows = [a for a in accounts if not selected or a.id in selected]

    def balance(account: AccountResponse) -> int:
        return effective_balance(
            current_balance=account.current_balance,
            balance_as_of=account.balance_as_of,
            classification=account.classification,
            activity_balance=account.activity_balance,
        )

    assets = sum(balance(a) for a in rows if a.classification != "liability")
    liabilities = sum(balance(a) for a in rows if a.classification == "liability")
    return {
        "assets": assets,
        "liabilities": liabilities,
        "net_worth": assets + liabilities,
    }


def net_worth_chart(points: list[NetWorthPoint]) -> dict[str, Any] | None:
    """A line needs two points; the series is empty until the nightly
    snapshot has run."""
    if len(points) < 2:
        return None
    return {
        "labels": [p.as_of_date.isoformat() for p in points],
        "series": [
            {"label": "Net worth", "values": [p.net_worth_amount / 100 for p in points]}
        ],
    }


def _month_label(key: str) -> str:
    """``"2026-05"`` -> ``"May '26"``; the year matters across a January."""
    year, _, month = key.partition("-")
    try:
        return f"{date(int(year), int(month), 1):%b} '{year[-2:]}"
    except ValueError:
        return key


def _fold_cashflow(months: list[CashflowMonth]) -> list[dict[str, Any]]:
    """At most ``MAX_CASHFLOW_BARS`` bars: months, then quarters, then years."""
    if len(months) <= MAX_CASHFLOW_BARS:
        return [
            {"label": _month_label(m.month), "income": m.income, "expense": m.expense}
            for m in months
        ]
    quarters = len(months) <= MAX_CASHFLOW_BARS * 3
    folded: dict[str, dict[str, Any]] = {}
    for m in months:
        year, _, month = m.month.partition("-")
        key = f"Q{(int(month or 1) - 1) // 3 + 1} '{year[-2:]}" if quarters else year
        row = folded.setdefault(key, {"label": key, "income": 0, "expense": 0})
        row["income"] += m.income
        row["expense"] += m.expense
    return list(folded.values())


def cashflow_chart(months: list[CashflowMonth]) -> dict[str, Any] | None:
    if not any(m.income or m.expense for m in months):
        return None
    bars = _fold_cashflow(months)
    return {
        "labels": [b["label"] for b in bars],
        "series": [
            {"label": "Income", "values": [b["income"] / 100 for b in bars]},
            {"label": "Spending", "values": [b["expense"] / 100 for b in bars]},
        ],
    }


def spending_chart(rows: list[SpendingCategory]) -> dict[str, Any] | None:
    """Top slices plus one "Other"; each slice carries the categories a
    drilldown should ask for, so "Other" expands to its members."""
    if not rows:
        return None
    top, tail = rows[:PIE_SLICES], rows[PIE_SLICES:]
    labels = [r.category for r in top]
    values = [r.amount / 100 for r in top]
    slices = [{"categories": [r.category]} for r in top]
    if tail:
        labels.append("Other")
        values.append(sum(r.amount for r in tail) / 100)
        slices.append({"categories": [r.category for r in tail]})
    return {
        "labels": labels,
        "series": [{"label": "Spent", "values": values}],
        "slices": slices,
    }


def upcoming_bills(projection: ProjectionResponse) -> list[dict[str, Any]]:
    """The window's bills, the shaping every "what is due" surface uses."""
    return upcoming_outflows(projection, limit=PREVIEW)


def ranked(
    rows: list[Any], label: str, value: str, count: str | None = None, tone: str = "teal"
) -> list[dict[str, Any]]:
    """Rows for ``ranked_rows``: the bar is each amount over the largest."""
    amounts = [abs(getattr(r, value) if not isinstance(r, dict) else r[value]) for r in rows]
    top = max(amounts, default=0) or 1

    def get(row: Any, key: str) -> Any:
        return row[key] if isinstance(row, dict) else getattr(row, key)

    return [
        {
            "label": get(r, label),
            "count": f"{get(r, count)}x" if count else "",
            "value": money(get(r, value)),
            "ratio": abs(get(r, value)) / top,
            "tone": tone,
        }
        for r in rows
    ]


def _query(days: int, account_ids: list[int]) -> str:
    return "&".join([f"days={days}", *(f"account_ids={i}" for i in account_ids)])


@router.get(SECTION.path, include_in_schema=False)
async def page(
    request: Request,
    days: int = Query(default=DEFAULT_DAYS, ge=1),
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    selected = account_ids or []
    window = ranges.horizon(days, MAX_DAYS)
    overview = await finance_overview(
        days=window,
        months=max(1, min(36, round(window / 30))),
        projection_days=30,
        preview_limit=PREVIEW,
        account_ids=account_ids,
        service=service,
        owner_user_id=owner_user_id,
    )
    pending = await service.list_pending_changes(owner_user_id=owner_user_id)
    return render(
        request,
        "pages/overview.html",
        {
            "section": SECTION,
            "days": days,
            "ranges": RANGES,
            "selected_ids": selected,
            "accounts": overview.accounts.items,
            "totals": totals(overview.accounts.items, selected),
            "net_worth": net_worth_chart(overview.net_worth),
            "cashflow": cashflow_chart(overview.cashflow.items),
            "spending": spending_chart(overview.spending),
            "drilldown": f"{SECTION.path}/spending?{_query(days, selected)}",
            "top_payees": overview.top_payees.items,
            "payee_rows": ranked(
                overview.top_payees.items, "payee", "amount", "transaction_count"
            ),
            "upcoming": upcoming_bills(overview.projection),
            "bill_rows": ranked(
                upcoming_bills(overview.projection), "name", "amount", tone="accent"
            ),
            "recent": overview.recent_transactions.items,
            "uncategorized": overview.uncategorized.items,
            "pending_count": len(pending),
            "txn_columns": TXN_COLUMNS,
            "payee_columns": PAYEE_COLUMNS,
            "bill_columns": BILL_COLUMNS,
        },
    )


@router.get(SECTION.path + "/spending", include_in_schema=False)
async def spending(
    request: Request,
    category: list[str] = Query(...),
    days: int = Query(default=DEFAULT_DAYS, ge=1),
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The transactions behind a donut slice, for the dialog (pattern 4).
    Several categories mean the "Other" slice."""
    rows = await spending_transactions(
        days=ranges.horizon(days, MAX_DAYS),
        categories=category,
        account_ids=account_ids,
        service=service,
        owner_user_id=owner_user_id,
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/overview/spending.html",
        context={
            "title": category[0] if len(category) == 1 else "Other",
            "days": days,
            "rows": rows.items,
            "txn_columns": TXN_COLUMNS,
        },
    )
