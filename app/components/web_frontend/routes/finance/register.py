"""The register: what fills the account detail column.

Not a router. ``register_filters`` is the query-string dependency the
account routes take; ``register_context`` turns an account (or none, for
the combined view) plus those filters into what the template renders:
transaction rows with a pager, or holdings and trades for an investment
account.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Query

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
from app.components.web_frontend.filters import mark_new
from app.core.formatting import payee_label
from app.services.finance.constants import INVESTMENT_ACCOUNT_TYPES
from app.services.finance.schemas import AccountResponse, TransactionResponse
from app.services.finance.service import (
    AXES,
    FinanceService,
    FinanceTransaction,
    account_names,
)
from app.services.finance.utils import money_to_cents

PAGE_SIZE = 50


Blank = ranges.Blank

# The columns a click can order by, and the only values ``sort`` takes.
SORTABLE = {"date", "account", "payee", "category", "amount"}

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
    {"key": "ticker", "label": "Ticker", "phone": "primary"},
    {"key": "quantity", "label": "Quantity", "align": "right"},
    {"key": "amount", "label": "Amount", "kind": "money", "align": "right"},
]


@dataclass(frozen=True)
class RegisterFilters:
    q: str | None = None
    categories: tuple[int, ...] = ()
    payees: tuple[int, ...] = ()
    tags: tuple[int, ...] = ()
    accounts: tuple[int, ...] = ()
    exclude: tuple[str, ...] = ()
    # Dollars, by magnitude, as typed: "$8" finds the $8.00 charge.
    amount_min: str | None = None
    amount_max: str | None = None
    # "yes" or "no": reviewed rows, or the ones not yet (#322).
    reviewed: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    # The chip row: a window in days, ALL for everything. An explicit
    # ``from`` beats it, so picking a date does not fight the chips.
    days: int = ranges.ALL
    # Everything shows by default; hiding transfers is the opt-out. Hidden by
    # default, Dad's mortgage (categorized Transfer) was invisible (#278).
    hide_transfers: bool = False
    # Fixed by a page rather than picked in the bar: the review queues are
    # the register with one of these on.
    uncategorized: bool = False
    without_merchant: bool = False
    page: int = 1
    page_size: int = PAGE_SIZE
    # Which visible column the rows are ordered by, and which way. Held
    # as a STRING rather than a bool because ``query`` drops falsy
    # values: a ``descending=False`` would vanish from the link that set
    # it and the table would spring back on the next click.
    sort: str | None = None
    sort_dir: str = "desc"

    @property
    def _picks(self) -> tuple[object, ...]:
        """The finer filters a reader picks in the bar."""
        return (
            self.categories,
            self.payees,
            self.tags,
            self.accounts,
            self.amount_min,
            self.amount_max,
            self.reviewed,
            self.from_date,
            self.to_date,
        )

    @property
    def narrowed(self) -> bool:
        """Whether a pick cuts the rows down (not the transfers opt-out)."""
        return any(self._picks)

    @property
    def folded(self) -> int:
        """How many filters sit behind a phone's Filters toggle."""
        return sum(map(bool, self._picks)) + self.hide_transfers

    def query(self, **overrides: Any) -> str:
        """The filters as a query string (``from``/``to`` in URL form)."""
        values = {**asdict(self), **overrides}
        names = {"from_date": ranges.FROM, "to_date": ranges.TO}
        pairs = {
            names.get(k, k): (v.isoformat() if isinstance(v, date) else v)
            for k, v in values.items()
            if v not in (None, "", False, ())
            and not (k == "page" and v == 1)
            and not (k == "page_size" and v == PAGE_SIZE)
            and not (k == "days" and v == ranges.ALL)
            and not (k == "sort_dir" and v == "desc")
        }
        for key, value in pairs.items():
            if value is True:
                pairs[key] = "on"
        return urlencode(pairs, doseq=True)

    def sorted_by(self, key: str) -> str:
        """The query string for clicking column ``key``: the same column
        again flips the direction, a different one starts at the top of
        that column. Paging resets, because page 3 of one order is
        nothing in another."""
        flipped = "asc" if self.sort == key and self.sort_dir == "desc" else "desc"
        return self.query(sort=key, sort_dir=flipped, page=1)

    def arrow(self, key: str) -> str:
        """What the header shows: nothing unless this is the sorted
        column, so four arrows do not compete for the reader."""
        if self.sort != key:
            return ""
        return "\u2191" if self.sort_dir == "asc" else "\u2193"

    @property
    def start_date(self) -> date | None:
        """Where the listing starts: the stated ``from``, else the chip."""
        return self.from_date or ranges.since(self.days)


# The reviewed filter's values, what they ask for, and how the select
# names them (#322).
REVIEWED = {"yes": True, "no": False}
REVIEWED_OPTIONS = [
    {"id": "yes", "name": "Reviewed"},
    {"id": "no", "name": "Not reviewed"},
]


def _cents(dollars: str | None) -> int | None:
    cents = money_to_cents(dollars) if dollars else None
    return abs(cents) if cents is not None else None


def _picks(rows: list[Any], chosen: tuple[int, ...]) -> list[dict[str, Any]]:
    """``checklist`` options: every row, ticked where it is picked."""
    return [{"value": r.id, "label": r.name, "checked": r.id in chosen} for r in rows]


def register_filters(
    q: str | None = None,
    categories: Annotated[list[int], Query()] = [],  # noqa: B006
    payees: Annotated[list[int], Query()] = [],  # noqa: B006
    tags: Annotated[list[int], Query()] = [],  # noqa: B006
    accounts: Annotated[list[int], Query()] = [],  # noqa: B006
    exclude: Annotated[list[str], Query()] = [],  # noqa: B006
    amount_min: Annotated[str | None, Blank] = None,
    amount_max: Annotated[str | None, Blank] = None,
    reviewed: Annotated[str | None, Blank] = None,
    # One value each, as links from elsewhere still name them.
    category_id: Annotated[int | None, Blank] = None,
    merchant_id: Annotated[int | None, Blank] = None,
    tag_id: Annotated[int | None, Blank] = None,
    from_date: ranges.FromDate = None,
    to_date: ranges.ToDate = None,
    days: Annotated[int, Blank] = ranges.ALL,
    hide_transfers: bool = False,
    uncategorized: bool = False,
    without_merchant: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=PAGE_SIZE, ge=1, le=200),
    sort: Annotated[str | None, Blank] = None,
    sort_dir: str = "desc",
) -> RegisterFilters:
    def picked(values: list[int], one: int | None) -> tuple[int, ...]:
        return tuple(dict.fromkeys([*values, *([one] if one else [])]))

    return RegisterFilters(
        q=q or None,
        categories=picked(categories, category_id),
        payees=picked(payees, merchant_id),
        tags=picked(tags, tag_id),
        accounts=tuple(dict.fromkeys(accounts)),
        exclude=tuple(a for a in AXES if a in exclude),
        amount_min=amount_min or None,
        amount_max=amount_max or None,
        reviewed=reviewed if reviewed in REVIEWED else None,
        from_date=from_date,
        to_date=to_date,
        days=days,
        hide_transfers=hide_transfers,
        uncategorized=uncategorized,
        without_merchant=without_merchant,
        page=page,
        page_size=page_size,
        # Anything else is not a column the reader can click, so it is
        # not an order the table will take.
        sort=sort if sort in SORTABLE else None,
        sort_dir="asc" if sort_dir == "asc" else "desc",
    )


def is_investment(account: AccountResponse | None) -> bool:
    return account is not None and account.account_type in INVESTMENT_ACCOUNT_TYPES


async def receipts(service: FinanceService, txns: list[Any]) -> dict[int, int]:
    """``{transaction id: its receipt's document id}`` for a page of rows,
    one read (#331): the paper filed on each by ``transaction_tag``. The
    page and every row action shape rows through ``_row`` with this."""
    from app.services.documents.service import tagged_with
    from app.services.finance.constants import transaction_tag

    filed = await tagged_with(service.db, [transaction_tag(t.id) for t in txns])
    return {t.id: ids[0] for t in txns if (ids := filed.get(transaction_tag(t.id)))}


def _row(
    txn: TransactionResponse, account_names: dict[int, str], receipt: int | None
) -> dict[str, Any]:
    return {
        "receipt": receipt,
        "id": txn.id,
        "account_id": txn.account_id,
        "date": txn.date,
        "account": account_names.get(txn.account_id, ""),
        "payee": payee_label(txn.merchant, txn.merchant_name, txn.name),
        "icon_url": txn.icon_url,
        "category": txn.category,
        "payee_category": txn.payee_category,
        "category_id": txn.category_id,
        "amount": txn.amount,
        "currency": txn.currency,
        "memo": txn.memo,
        "tags": txn.tags,
        "is_split": txn.is_split,
        "pending": txn.pending,
        "is_reviewed": txn.is_reviewed,
        "transfer_pair": txn.transfer_pair_transaction_id,
        "splits": txn.splits,
        "import_batch_id": txn.import_batch_id,
        "created_at": txn.created_at,
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
    # By the rows' own account ids, so a row on somebody else's account
    # is named too - a page of "ours" would leave it blank.
    names = await account_names(service.db, [item.account_id for item in items])
    filed = await receipts(service, items)
    return {
        "rows": [
            {
                **_row(item, names, filed.get(item.id)),
                "suggestion": (suggestions or {}).get(item.id),
            }
            for item in items
        ],
        "categories": (
            await list_category_options(service=service, owner_user_id=owner_user_id)
        ).items,
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
    seen: datetime | None = None,
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
        # One watermark for the page, not one per table: Holdings and
        # Activity are two tables on the Register tab, seen in the same
        # glance, and a second cookie would mark one of them as unread
        # while the reader was looking straight at it.
        return {
            "kind": "investment",
            "holdings": await mark_new(
                service.db,
                [
                    {
                        "ticker": h.ticker,
                        "name": h.name,
                        "quantity": f"{h.quantity:g}",
                        "price": None
                        if h.price is None
                        else round(h.price * 100 / 10**h.price_scale),
                        "value": h.market_value,
                        "import_batch_id": h.import_batch_id,
                        "created_at": h.created_at,
                    }
                    for h in holdings.items
                ],
                seen,
            ),
            "portfolio_value": holdings.portfolio_value,
            "trades": await mark_new(
                service.db,
                [
                    {
                        "date": t.trade_date,
                        "type": t.type.replace("_", " ").title(),
                        "ticker": tickers.get(t.security_id) or t.name,
                        "quantity": None if t.quantity is None else f"{t.quantity:g}",
                        "amount": t.amount,
                        "import_batch_id": t.import_batch_id,
                        "created_at": t.created_at,
                    }
                    for t in trades.items
                ],
                seen,
            ),
            "holding_columns": HOLDING_COLUMNS,
            "trade_columns": TRADE_COLUMNS,
        }

    listing = await list_transactions(
        sort=filters.sort,
        descending=filters.sort_dir != "asc",
        account_id=account.id if account else None,
        account_ids=None,
        from_date=filters.start_date,
        to_date=filters.to_date,
        without_merchant=filters.without_merchant,
        q=filters.q,
        categories=list(filters.categories),
        payees=list(filters.payees),
        tags=list(filters.tags),
        accounts=list(filters.accounts),
        exclude=list(filters.exclude),
        amount_min=_cents(filters.amount_min),
        amount_max=_cents(filters.amount_max),
        reviewed=REVIEWED.get(filters.reviewed or ""),
        include_transfers=not filters.hide_transfers,
        uncategorized=filters.uncategorized,
        page=filters.page,
        page_size=filters.page_size,
        service=service,
        owner_user_id=owner_user_id,
    )
    # By the rows' own accounts, like the re-rendered rows: a closed
    # account is out of the picker and still names its rows (#315).
    names = await account_names(service.db, [t.account_id for t in listing.items])
    filed = await receipts(service, listing.items)
    categories = (
        await list_category_options(service=service, owner_user_id=owner_user_id)
    ).items
    merchants = (
        await list_merchants(
            account_ids=None, service=service, owner_user_id=owner_user_id
        )
    ).items
    tags = await list_tags(service=service, owner_user_id=owner_user_id)
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
        "rows": await mark_new(
            service.db,
            [_row(t, names, filed.get(t.id)) for t in listing.items],
            seen,
        ),
        "total": listing.total,
        "pager": _pager(path, filters, listing.total),
        "categories": categories,
        "tags": tags,
        "reviewed_options": REVIEWED_OPTIONS,
        # Each axis's checklist (#326): every value, ticked where picked.
        "picks": {
            axis: _picks(rows, getattr(filters, axis))
            for axis, rows in (
                ("categories", categories),
                ("payees", merchants),
                ("tags", tags),
                ("accounts", accounts if account is None else []),
            )
        },
        "uncategorized_total": await uncategorized_total(service, owner_user_id),
        "show_account": account is None,
        "filtered": bool(filters.days != ranges.ALL or filters.q or filters.narrowed),
    }
