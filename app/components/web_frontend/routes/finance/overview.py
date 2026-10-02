"""The overview section: the composite overview, rendered.

One in-process call to the API's ``finance_overview`` (one DB session,
eight sub-reads) and a little presentation shaping, all pure functions
below so they are testable without a request.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.categories import spending_transactions
from app.components.backend.api.finance.overview import (
    finance_overview,
    net_worth_by_type,
    net_worth_series,
)
from app.components.web_frontend import ranges
from app.components.web_frontend.filters import account_params, dollars, money
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import hx_dialog, render, templates
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger.accounts import effective_balance
from app.services.finance.domains.ledger.merchant_icon import Icon, payee_icons_by_name
from app.services.finance.domains.planning.recurring.forecast import upcoming_outflows
from app.services.finance.schemas import (
    AccountResponse,
    CashflowMonth,
    CategoryMove,
    NetWorthByType,
    NetWorthPoint,
    ProjectionResponse,
    SpendingCategory,
    SpendingPace,
)
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from app.services.matters.deadlines import due_soon, expected_soon

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
    {"key": "payee", "label": "Payee", "kind": "avatar"},
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


# The net worth card's views (#343): the field's value and its chip.
WORTH_VIEWS: tuple[tuple[str, str], ...] = (
    ("net", "Net"),
    ("split", "Assets & debts"),
    ("type", "By type"),
)


def net_worth_chart(
    points: list[NetWorthPoint], worth: str = "net"
) -> dict[str, Any] | None:
    """A line needs two points; the series is empty until the nightly
    snapshot has run. ``split`` draws assets and debts apart."""
    if len(points) < 2:
        return None
    lines = (
        [("Assets", "total_assets_amount"), ("Debts", "total_liabilities_amount")]
        if worth == "split"
        else [("Net worth", "net_worth_amount")]
    )
    return {
        "labels": [p.as_of_date.isoformat() for p in points],
        "series": [
            {"label": label, "values": [dollars(getattr(p, field)) for p in points]}
            for label, field in lines
        ],
    }


def type_chart(
    parts: NetWorthByType,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """By type: a line per account group, and what each did over the
    window, first day to last - where a drop came from."""
    if len(parts.dates) < 2:
        return None, []
    chart = {
        "labels": [day.isoformat() for day in parts.dates],
        "series": [
            {"label": g.label, "values": [dollars(v) for v in g.values]}
            for g in parts.groups
        ],
    }
    change = [
        {"label": g.label, "change": g.values[-1] - g.values[0]} for g in parts.groups
    ]
    return chart, change


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
            {"label": "Income", "values": [dollars(b["income"]) for b in bars]},
            {"label": "Spending", "values": [dollars(b["expense"]) for b in bars]},
        ],
    }


def spending_chart(rows: list[SpendingCategory]) -> dict[str, Any] | None:
    """Top slices plus one "Other"; each slice carries the categories a
    drilldown should ask for, so "Other" expands to its members."""
    if not rows:
        return None
    top, tail = rows[:PIE_SLICES], rows[PIE_SLICES:]
    labels = [r.category for r in top]
    values = [dollars(r.amount) for r in top]
    slices = [{"categories": [r.category]} for r in top]
    if tail:
        labels.append("Other")
        values.append(dollars(sum(r.amount for r in tail)))
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
    rows: list[Any],
    label: str,
    value: str,
    count: str | None = None,
    tone: str = "teal",
    icons: dict[str, Icon] | None = None,
) -> list[dict[str, Any]]:
    """Rows for ``ranked_rows``: the bar is each amount over the largest;
    ``icons`` maps a label to its brand mark URL."""
    amounts = [
        abs(getattr(r, value) if not isinstance(r, dict) else r[value]) for r in rows
    ]
    top = max(amounts, default=0) or 1

    def get(row: Any, key: str) -> Any:
        return row[key] if isinstance(row, dict) else getattr(row, key, None)

    return [
        {
            "label": get(r, label),
            "icon_url": icon.url
            if (icon := (icons or {}).get(get(r, label)))
            else None,
            "category": get(r, "category"),
            "count": f"{get(r, count)}x" if count else "",
            "value": money(get(r, value)),
            "ratio": abs(get(r, value)) / top,
            "tone": tone,
        }
        for r in rows
    ]


# What this month's line is drawn against (#305): the field, its chip, and
# the series' name.
COMPARES: tuple[tuple[str, str, str], ...] = (
    ("average", "Average", "Average month"),
    ("median", "Median", "Median month"),
    ("last_month", "Last month", "Last month"),
)


def pace_chart(pace: SpendingPace, compare: str) -> dict[str, Any] | None:
    """This month by day, filled, against the chosen month as a dashed line;
    None until there is spending to draw."""
    if not any(pace.this_month) and not any(pace.average):
        return None
    label = next(name for key, _chip, name in COMPARES if key == compare)
    return {
        "labels": [str(day) for day in range(1, pace.days + 1)],
        "series": [
            {"label": "This month", "values": [dollars(v) for v in pace.this_month]},
            {
                "label": label,
                "values": [dollars(v) for v in getattr(pace, compare)],
                "compare": True,
            },
        ],
    }


def move_rows(moves: list[CategoryMove]) -> list[dict[str, Any]]:
    """``ranked_rows`` for What moved (#346): this month so far, beside what
    a usual month had spent by now; the bar warm when it is up. The
    month's rows are a click away."""
    days = current_date().day
    rows = ranked(moves, "name", "this_month")
    for row, move in zip(rows, moves, strict=True):
        url = f"{SECTION.path}/spending?{urlencode({'category': move.name, 'days': days})}"
        row.update(
            category=move.name,
            count="new"
            if move.typical is None
            else f"usually {money(move.typical)} by now",
            tone="accent" if move.change > 0 else "teal",
            attrs=hx_dialog(url),
        )
    return rows


async def _net_worth_card(
    service: FinanceService,
    owner_user_id: int | None,
    window: int,
    end: date | None,
    account_ids: list[int] | None,
    points: list[NetWorthPoint],
    kept: dict[str, str],
) -> dict[str, Any]:
    """The net worth card's chart and, by type, what each group did. The
    composite's line is the house-in net; any other view reads its own."""
    without_house = kept["house"] == "out"
    if kept["worth"] == "type":
        chart, change = type_chart(
            await net_worth_by_type(
                days=window,
                account_ids=account_ids,
                without_house=without_house,
                end=end,
                service=service,
                owner_user_id=owner_user_id,
            )
        )
        return {"net_worth": chart, "net_worth_change": change}
    if without_house:
        points = await net_worth_series(
            days=window,
            account_ids=account_ids,
            without_house=True,
            end=end,
            service=service,
            owner_user_id=owner_user_id,
        )
    return {"net_worth": net_worth_chart(points, kept["worth"]), "net_worth_change": []}


def _query(
    days: int,
    account_ids: list[int],
    start: date | None = None,
    end: date | None = None,
) -> str:
    """The filter as a query string: the chips' window, the accounts, and
    a picked from-to range (#342) when there is one."""
    picked = ranges.date_params(start, end)
    return "&".join(
        [
            f"days={days}",
            *account_params(account_ids),
            *([urlencode(picked)] if picked else []),
        ]
    )


@router.get(SECTION.path, include_in_schema=False)
async def page(
    request: Request,
    picked: ranges.PickedRange,
    days: int = Query(default=DEFAULT_DAYS, ge=1),
    compare: str = "average",
    worth: str = "net",
    house: str = "in",
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    selected = account_ids or []
    start, end = picked
    # The cards' own choices, kept in the URL; the filter form carries them.
    kept = {
        "compare": compare if compare in {k for k, *_ in COMPARES} else "average",
        "worth": worth if worth in dict(WORTH_VIEWS) else "net",
        "house": "out" if house == "out" else "in",
    }

    def link(**change: str) -> str:
        """The page with one choice changed and everything else kept."""
        query = _query(days, selected, start, end)
        return f"{SECTION.path}?{query}&{urlencode({**kept, **change})}"

    window = ranges.horizon(days, MAX_DAYS, start)
    overview = await finance_overview(
        days=window,
        months=max(1, min(36, round(window / 30))),
        end=end,
        projection_days=30,
        preview_limit=PREVIEW,
        account_ids=account_ids,
        service=service,
        owner_user_id=owner_user_id,
    )
    worth_card = await _net_worth_card(
        service, owner_user_id, window, end, account_ids, overview.net_worth, kept
    )
    pending = await service.list_pending_changes(owner_user_id=owner_user_id)
    upcoming = upcoming_bills(overview.projection)
    # The cards know payees by name; one resolution serves both.
    icons = await payee_icons_by_name(
        service.db,
        [p.payee for p in overview.top_payees.items] + [b["name"] for b in upcoming],
        owner_user_id=owner_user_id,
    )
    response = render(
        request,
        "pages/overview.html",
        {
            "section": SECTION,
            "days": days,
            "ranges": RANGES,
            "selected_ids": selected,
            "accounts": overview.accounts.items,
            "totals": totals(overview.accounts.items, selected),
            **worth_card,
            "worth_views": [
                *(
                    (chip, key == kept["worth"], link(worth=key))
                    for key, chip in WORTH_VIEWS
                ),
                (
                    "Without the house",
                    kept["house"] == "out",
                    link(house="in" if kept["house"] == "out" else "out"),
                ),
            ],
            "kept": kept,
            "cashflow": cashflow_chart(overview.cashflow.items),
            "spending": spending_chart(overview.spending),
            "drilldown": f"{SECTION.path}/spending?{_query(days, selected, start, end)}",
            "dates": (start, end),
            "top_payees": overview.top_payees.items,
            "payee_rows": ranked(
                overview.top_payees.items,
                "payee",
                "amount",
                "transaction_count",
                icons=icons,
            ),
            "move_rows": move_rows(overview.category_moves),
            "pace": pace_chart(overview.pace, kept["compare"]),
            "pace_today": overview.pace.this_month[-1],
            "pace_usual": getattr(overview.pace, kept["compare"])[
                len(overview.pace.this_month) - 1
            ],
            "compares": [
                (chip, key == kept["compare"], link(compare=key))
                for key, chip, _name in COMPARES
            ],
            "upcoming": upcoming,
            "bill_rows": ranked(upcoming, "name", "amount", tone="accent", icons=icons),
            "recent": overview.recent_transactions.items,
            "uncategorized": overview.uncategorized.items,
            "pending_count": len(pending),
            # What a matter is owed, where the reader already looks. The
            # sidebar's dot only ever appeared once the day had passed.
            "deadlines_count": len(await due_soon(service.db)),
            "expected_count": len(await expected_soon(service.db)),
            "txn_columns": TXN_COLUMNS,
            "payee_columns": PAYEE_COLUMNS,
            "bill_columns": BILL_COLUMNS,
        },
    )
    return ranges.remember(response, start, end)


@router.get(SECTION.path + "/spending", include_in_schema=False)
async def spending(
    request: Request,
    category: list[str] = Query(...),
    days: int = Query(default=DEFAULT_DAYS, ge=1),
    start: ranges.FromDate = None,
    end: ranges.ToDate = None,
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The transactions behind a donut slice, for the dialog (pattern 4).
    Several categories mean the "Other" slice."""
    rows = await spending_transactions(
        days=ranges.horizon(days, MAX_DAYS, start),
        categories=category,
        account_ids=account_ids,
        end=end,
        service=service,
        owner_user_id=owner_user_id,
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/transactions_dialog.html",
        context={
            "title": category[0] if len(category) == 1 else "Other",
            "subtitle": f"{start} to {end or 'today'}"
            if start
            else f"last {days} days",
            "rows": rows.items,
            "columns": TXN_COLUMNS,
            "empty": "No transactions behind this slice",
        },
    )
