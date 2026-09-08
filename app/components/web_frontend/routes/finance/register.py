"""The register: what fills the account detail column.

Not a router. ``register_filters`` is the query-string dependency the
account routes take; ``register_context`` turns an account (or none, for
the combined view) plus those filters into what the template renders:
transaction rows with a pager, or holdings and trades for an investment
account.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Query
from pydantic import BeforeValidator

from app.components.backend.api.finance.categories import list_category_options
from app.components.backend.api.finance.investments import (
    list_account_holdings,
    list_account_trades,
)
from app.components.backend.api.finance.payees import list_merchants
from app.components.backend.api.finance.register import (
    hydrate_transactions,
    list_tags,
    list_transactions,
    uncategorized_transactions,
)
from app.components.web_frontend import ranges
from app.services.finance.constants import INVESTMENT_ACCOUNT_TYPES
from app.services.finance.models import FinanceTransaction
from app.services.finance.schemas import AccountResponse, TransactionResponse
from app.services.finance.service import FinanceService

PAGE_SIZE = 50


def _blank_is_none(value: Any) -> Any:
    """A submitted form sends ``""`` for an untouched select or date input."""
    return None if value == "" else value


Blank = BeforeValidator(_blank_is_none)

TXN_COLUMNS = [
    {"key": "date", "label": "Date", "kind": "date"},
    {"key": "account", "label": "Account"},
    {"key": "payee", "label": "Payee"},
    {"key": "category", "label": "Category"},
    {"key": "amount", "label": "Amount", "kind": "money", "align": "right"},
]
HOLDING_COLUMNS = [
    {"key": "ticker", "label": "Ticker"},
    {"key": "name", "label": "Name"},
    {"key": "quantity", "label": "Quantity", "align": "right"},
    {"key": "price", "label": "Price", "kind": "money", "align": "right"},
    {"key": "value", "label": "Value", "kind": "money", "align": "right"},
]
TRADE_COLUMNS = [
    {"key": "date", "label": "Date", "kind": "date"},
    {"key": "type", "label": "Type"},
    {"key": "ticker", "label": "Ticker"},
    {"key": "quantity", "label": "Quantity", "align": "right"},
    {"key": "amount", "label": "Amount", "kind": "money", "align": "right"},
]


@dataclass(frozen=True)
class RegisterFilters:
    q: str | None = None
    category_id: int | None = None
    merchant_id: int | None = None
    tag_id: int | None = None
    from_date: date | None = None
    to_date: date | None = None
    # The chip row: a window in days, ALL for everything. An explicit
    # ``from`` beats it, so picking a date does not fight the chips.
    days: int = ranges.ALL
    include_transfers: bool = False
    # Fixed by a page rather than picked in the bar: the review queues are
    # the register with one of these on.
    uncategorized: bool = False
    without_merchant: bool = False
    page: int = 1
    page_size: int = PAGE_SIZE

    def query(self, **overrides: Any) -> str:
        """The filters as a query string (``from``/``to`` in URL form)."""
        values = {**asdict(self), **overrides}
        names = {"from_date": "from", "to_date": "to"}
        pairs = {
            names.get(k, k): (v.isoformat() if isinstance(v, date) else v)
            for k, v in values.items()
            if v not in (None, "", False)
            and not (k == "page" and v == 1)
            and not (k == "page_size" and v == PAGE_SIZE)
            and not (k == "days" and v == ranges.ALL)
        }
        for key, value in pairs.items():
            if value is True:
                pairs[key] = "on"
        return urlencode(pairs)

    @property
    def start_date(self) -> date | None:
        """Where the listing starts: the stated ``from``, else the chip."""
        return self.from_date or ranges.since(self.days)


def register_filters(
    q: str | None = None,
    category_id: Annotated[int | None, Blank] = None,
    merchant_id: Annotated[int | None, Blank] = None,
    tag_id: Annotated[int | None, Blank] = None,
    from_date: Annotated[date | None, Blank, Query(alias="from")] = None,
    to_date: Annotated[date | None, Blank, Query(alias="to")] = None,
    days: Annotated[int, Blank] = ranges.ALL,
    include_transfers: bool = False,
    uncategorized: bool = False,
    without_merchant: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=PAGE_SIZE, ge=1, le=200),
) -> RegisterFilters:
    return RegisterFilters(
        q=q or None,
        category_id=category_id,
        merchant_id=merchant_id,
        tag_id=tag_id,
        from_date=from_date,
        to_date=to_date,
        days=days,
        include_transfers=include_transfers,
        uncategorized=uncategorized,
        without_merchant=without_merchant,
        page=page,
        page_size=page_size,
    )


def is_investment(account: AccountResponse | None) -> bool:
    return account is not None and account.account_type in INVESTMENT_ACCOUNT_TYPES


def _row(txn: TransactionResponse, account_names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": txn.id,
        "account_id": txn.account_id,
        "date": txn.date,
        "account": account_names.get(txn.account_id, ""),
        "payee": txn.merchant or txn.merchant_name or txn.name,
        "category": txn.category,
        "category_id": txn.category_id,
        "amount": txn.amount,
        "currency": txn.currency,
        "tags": txn.tags,
        "is_split": txn.is_split,
        "splits": txn.splits,
    }


async def rows_context(
    service: FinanceService,
    txns: list[FinanceTransaction],
    owner_user_id: int | None,
    suggestions: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What re-rendered rows need (pattern 2): the shaped rows, the category
    options, and the uncategorised count for the OOB chip. One hydration
    for one row or fifty. ``suggestions`` (transaction id -> {category_id,
    category_name}) rides on the row as a preview nothing has written."""
    items = await hydrate_transactions(service, txns)
    accounts, _ = await service.list_accounts(
        owner_user_id=owner_user_id, page=1, page_size=500
    )
    names = {a.id: a.name for a in accounts}
    return {
        "rows": [
            {**_row(item, names), "suggestion": (suggestions or {}).get(item.id)}
            for item in items
        ],
        "categories": (await list_category_options(service=service)).items,
        "uncategorized_total": await uncategorized_total(service, owner_user_id),
    }


async def uncategorized_total(
    service: FinanceService, owner_user_id: int | None
) -> int:
    listing = await uncategorized_transactions(
        limit=1,
        q=None,
        from_date=None,
        account_ids=None,
        service=service,
        owner_user_id=owner_user_id,
    )
    return listing.total


def _pager(path: str, filters: RegisterFilters, total: int) -> dict[str, Any] | None:
    pages = max(1, -(-total // filters.page_size))
    if pages == 1:
        return None
    start = (filters.page - 1) * filters.page_size + 1
    end = min(filters.page * filters.page_size, total)
    link = lambda n: f"{path}?{filters.query(page=n)}"  # noqa: E731
    return {
        "start": start,
        "end": end,
        "total": total,
        "prev": link(filters.page - 1) if filters.page > 1 else None,
        "next": link(filters.page + 1) if filters.page < pages else None,
    }


async def register_context(
    *,
    path: str,
    account: AccountResponse | None,
    accounts: list[AccountResponse],
    filters: RegisterFilters,
    service: FinanceService,
    owner_user_id: int | None,
) -> dict[str, Any]:
    if is_investment(account):
        assert account is not None
        holdings = await list_account_holdings(
            account.id, service=service, owner_user_id=owner_user_id
        )
        trades = await list_account_trades(
            account.id, service=service, owner_user_id=owner_user_id
        )
        # The trade schema names no ticker; the account's positions know it.
        tickers = {h.security_id: h.ticker for h in holdings.items}
        return {
            "kind": "investment",
            "holdings": [
                {
                    "ticker": h.ticker,
                    "name": h.name,
                    "quantity": f"{h.quantity:g}",
                    "price": None
                    if h.price is None
                    else round(h.price * 100 / 10**h.price_scale),
                    "value": h.market_value,
                }
                for h in holdings.items
            ],
            "portfolio_value": holdings.portfolio_value,
            "trades": [
                {
                    "date": t.trade_date,
                    "type": t.type.replace("_", " ").title(),
                    "ticker": tickers.get(t.security_id) or t.name,
                    "quantity": None if t.quantity is None else f"{t.quantity:g}",
                    "amount": t.amount,
                }
                for t in trades.items
            ],
            "holding_columns": HOLDING_COLUMNS,
            "trade_columns": TRADE_COLUMNS,
        }

    listing = await list_transactions(
        account_id=account.id if account else None,
        account_ids=None,
        from_date=filters.start_date,
        to_date=filters.to_date,
        category_id=filters.category_id,
        merchant_id=filters.merchant_id,
        without_merchant=filters.without_merchant,
        tag_id=filters.tag_id,
        q=filters.q,
        include_transfers=filters.include_transfers,
        uncategorized=filters.uncategorized,
        page=filters.page,
        page_size=filters.page_size,
        service=service,
        owner_user_id=owner_user_id,
    )
    names = {a.id: a.name for a in accounts}
    columns = (
        TXN_COLUMNS
        if account is None
        else [c for c in TXN_COLUMNS if c["key"] != "account"]
    )
    return {
        "kind": "transactions",
        "path": path,
        "filters": filters,
        "ranges": ranges.WINDOWS,
        "columns": columns,
        "rows": [_row(t, names) for t in listing.items],
        "total": listing.total,
        "pager": _pager(path, filters, listing.total),
        "categories": (await list_category_options(service=service)).items,
        "merchants": (
            await list_merchants(
                account_ids=None, service=service, owner_user_id=owner_user_id
            )
        ).items,
        "tags": await list_tags(service=service, owner_user_id=owner_user_id),
        "uncategorized_total": await uncategorized_total(service, owner_user_id),
        "show_account": account is None,
        "filtered": bool(
            filters.days != ranges.ALL
            or filters.q
            or filters.category_id
            or filters.merchant_id
            or filters.tag_id
            or filters.from_date
            or filters.to_date
        ),
    }
