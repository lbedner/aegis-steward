"""Finance host tools: the data surface code-mode agents compute over.

Deliberately few and wide: results land in the code-mode sandbox rather
than the prompt, so a broad payload the model slices in code beats a
catalog of narrow question-shaped tools. Each tool is a thin, fully-typed
composition of existing domain queries, registered into the agent tool
registry at import time. A code-mode agent granted these names calls them
as functions from inside its sandboxed script; a plain agent calls them
as ordinary tools.

Returns are plain JSON-safe dicts (cents-integer money, ISO dates) so
values traverse the sandbox boundary without model-object baggage.

Owner scoping: tools read unscoped (``owner_user_id=None``), matching
the single-tenant default-open posture of generated stacks. A
multi-tenant deployment should thread its user context here before
granting these tools to shared agents.
"""

from datetime import date
from typing import Any

from app.core.db import get_async_session
from app.core.formatting import payee_label
from app.services.ai.domains.chat.tools import register_tool
from app.services.finance.constants import UNCATEGORIZED_CATEGORY_NAMES
from app.services.finance.domains.investments import queries as investment_queries
from app.services.finance.domains.ledger.queries.accounts import EVERYONE, accounts_page
from app.services.finance.domains.ledger.queries.categories import (
    category_names_by_id,
)
from app.services.finance.domains.ledger.queries.transactions import (
    transactions_window_with_payees,
)
from app.services.finance.domains.ledger.transactions import (
    monthly_cashflow,
    transaction_tags,
)
from app.services.finance.utils import current_date

# Sized for years of a personal ledger (a few thousand rows/year); rows
# land in the code-mode sandbox, not the model's context, so the cost of
# a big window is a few ms of marshaling. ``total`` in the payload still
# exposes truncation, so a capped result never reads as complete.
_TRANSACTIONS_ROW_CAP = 5000

# How far ahead accounts() reports scheduled flows: one full monthly
# cycle with margin, so a mid-month plan sees next month's rent.
_UPCOMING_WINDOW_DAYS = 35


def _months_back_start(today: date, span: int) -> date:
    """First day of the month ``span - 1`` calendar months before today."""
    total_months = today.year * 12 + today.month - 1 - (span - 1)
    return date(total_months // 12, total_months % 12 + 1, 1)


async def ledger(months: int = 12, detail: str = "monthly") -> dict[str, Any]:
    """Cash flow from the transaction ledger.

    Args:
        months: How many months back to include (1-24).
        detail: "monthly" or "transactions".

    Monthly detail returns a dict with key 'months': a list, oldest
    first, of entries carrying 'month', 'income_cents', 'spend_cents'
    and 'net_cents'. Transaction detail returns keys 'total', 'returned'
    and 'transactions': a list, newest first, of entries carrying
    'date', 'payee', 'amount_cents' (signed, negative = outflow),
    'category', 'account' and 'pending'; fewer returned rows than
    'total' means the page was capped.
    """
    if detail == "monthly":
        span = max(1, min(int(months), 24))
        async with get_async_session() as session:
            rows = await monthly_cashflow(session, months=span)
        return {
            "months": [
                {
                    "month": row.month,
                    "income_cents": row.income,
                    "spend_cents": row.expense,
                    "net_cents": row.net,
                }
                for row in rows
            ]
        }
    if detail != "transactions":
        raise ValueError('detail must be "monthly" or "transactions"')

    span = max(1, min(int(months), 24))
    from_date = _months_back_start(current_date(), span)
    async with get_async_session() as session:
        rows, total = await transactions_window_with_payees(
            session,
            from_date=from_date,
            limit=_TRANSACTIONS_ROW_CAP,
        )
        category_names = await category_names_by_id(
            session,
            {t.category_id for t, _name in rows if t.category_id is not None},
        )
        account_rows, _count = await accounts_page(
            session,
            owner_user_id=None,
            include_hidden=True,
            page=1,
            page_size=500,
            subject_id=EVERYONE,
        )
        tags_by_txn = await transaction_tags(
            session, [t.id for t, _name in rows if t.id is not None]
        )
    account_names = {account.id: account.name for account in account_rows}
    return {
        "total": total,
        "returned": len(rows),
        "transactions": [
            {
                # The id is what a transaction.categorize proposal's
                # 'transaction_id' takes - names alone cannot propose.
                "id": txn.id,
                "date": txn.date_.isoformat(),
                # Curated merchant name first - what the register shows -
                # then the provider's merchant string, then the raw
                # descriptor. A renamed payee that only lived in the
                # curation layer was invisible to the assistant.
                "payee": payee_label(curated_name, txn.merchant_name, txn.name),
                "amount_cents": txn.amount,
                "category": category_names.get(txn.category_id),
                "category_id": txn.category_id,
                # True for BOTH shapes of uncategorized: no category at
                # all, or the import catch-all bucket ("Uncategorized",
                # "Misc", ...). Filtering category is None alone misses
                # the second shape - a model burned four runs on that.
                "uncategorized": txn.category_id is None
                or (category_names.get(txn.category_id) or "").lower()
                in UNCATEGORIZED_CATEGORY_NAMES,
                "account": account_names.get(txn.account_id),
                # The label axis, orthogonal to category ("Business" on
                # a Software row) - what tag rollups compute over.
                "tags": sorted(t.name for t in tags_by_txn.get(txn.id, [])),
                "pending": txn.pending,
            }
            for txn, curated_name in rows
        ],
    }


async def quote(ticker: str) -> dict[str, Any]:
    """Latest stored closing price for a ticker in the security catalog.

    Args:
        ticker: The security's ticker symbol, case-insensitive.
    """
    symbol = ticker.strip().upper()
    async with get_async_session() as session:
        security = await investment_queries.security_by_ticker(session, symbol)
        if security is None or security.id is None:
            return {"found": False, "ticker": symbol}
        price = await investment_queries.latest_price_for_security(session, security.id)
    if price is None:
        return {"found": False, "ticker": symbol}
    # Normalize to cents in integer arithmetic regardless of the row's
    # stored scale; float exponents would drift on sub-cent scales.
    if price.price_scale > 2:
        divisor = 10 ** (price.price_scale - 2)
        close_cents = (price.close_price + divisor // 2) // divisor
    else:
        close_cents = price.close_price * 10 ** (2 - price.price_scale)
    as_of: date = price.price_date
    return {
        "found": True,
        "ticker": symbol,
        "name": security.name,
        "close_cents": close_cents,
        "as_of": as_of.isoformat(),
    }


# Built-in registration: importing this module makes the tools grantable
# via the agent registry. replace=True keeps re-imports idempotent.


async def projection(
    days: int = 180,
    account_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Cash walked forward from today through the scheduled bills and
    income, over any window: 'as_of', 'horizon_days', 'start_balance',
    'end_balance', 'upcoming_total' (signed cents, net of the window),
    'bills' (what is due, positive amounts, soonest first) and 'points'
    (every occurrence with the running 'balance' after it, so the low
    point and the date it happens are read off the walk rather than
    re-derived).

    ``days`` is the window - 365 for a year, 30 for the month - and
    ``account_ids`` narrows the starting balance to particular accounts
    ("only the checking, not savings"). Without this the only
    forward-looking number available was the fixed 60-day figure in the
    briefing, so any other horizon had to be hand-rolled from the bill
    list and then disowned as untrustworthy.
    """
    from app.services.finance.domains.planning.recurring.forecast import (
        project_balances,
        upcoming_outflows,
    )

    async with get_async_session() as session:
        walk = await project_balances(
            session, owner_user_id=None, days=days, account_ids=account_ids
        )
    return {
        "as_of": walk.as_of.isoformat(),
        "horizon_days": walk.horizon_days,
        "start_balance": walk.start_balance,
        "end_balance": walk.end_balance,
        "upcoming_total": walk.upcoming_total,
        "bills": [
            {
                **bill,
                "date": bill["date"].isoformat(),
                "due_date": bill["due_date"].isoformat() if bill["due_date"] else None,
            }
            for bill in upcoming_outflows(walk)
        ],
        "points": [
            {
                "date": p.date.isoformat(),
                "name": p.name,
                "direction": p.direction,
                "amount": p.amount,
                "balance": p.balance,
                "account": p.account,
                "category": p.category,
            }
            for p in walk.points
        ],
    }


async def transactions(
    payee: str | None = None,
    amount_cents: int | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Find PARTICULAR transactions, without reading the ledger.

    Every filter is optional and they narrow together: ``payee`` matches
    the payee or the raw descriptor, ``amount_cents`` matches by
    magnitude (800 finds a $8.00 charge whichever way it is signed),
    ``since``/``until`` are ISO dates. Returns 'total' (how many match)
    and 'transactions' - id, date, payee, amount_cents, category,
    account, memo - newest first, capped at ``limit``.

    Use this, not ``ledger(detail="transactions")``, whenever the
    question names a payee, an amount or a date: matching a receipt to
    a charge used to mean pulling months of ledger into the sandbox and
    filtering in Python, which is slow, easy to get wrong, and pulled
    two years of rows to find three. ``ledger`` is for the SHAPE of
    spending; this is for the rows.
    """
    from app.components.backend.api.finance.register import hydrate_transactions
    from app.services.finance.service import FinanceService

    def _date(raw: str | None) -> date | None:
        return date.fromisoformat(raw) if raw else None

    async with get_async_session() as session:
        service = FinanceService(session)
        rows, total = await service.list_transactions(
            owner_user_id=None,
            query=payee or None,
            amount=amount_cents,
            from_date=_date(since),
            to_date=_date(until),
            page_size=max(1, min(int(limit), 200)),
        )
        items = await hydrate_transactions(service, rows)
        accounts = {
            account.id: account.name
            for account in (
                await accounts_page(
                    session,
                    owner_user_id=None,
                    include_hidden=True,
                    page=1,
                    page_size=500,
                    subject_id=EVERYONE,
                )
            )[0]
        }
    return {
        "total": total,
        "returned": len(items),
        "transactions": [
            {
                "id": item.id,
                "date": item.date.isoformat(),
                "payee": item.payee,
                "amount_cents": item.amount,
                "category": item.category,
                "account": accounts.get(item.account_id, ""),
                "memo": item.memo,
            }
            for item in items
        ],
    }


async def budget(period_month: int | None = None) -> dict[str, Any]:
    """The limits the user actually set, and how the month is going
    against them: 'period_month' (YYYYMM), 'limits' (every FLEXIBLE
    line - 'category' or 'payee', 'limit' and 'spent' in cents,
    'remaining', and 'status' of good/warn/critical), 'commitments' (the
    recurring bills shown for context, which are NOT limits anyone set),
    and 'stats' (the month's totals, how many limits are over, and the
    days left in the period).

    Reach for this whenever the question is what something is BUDGETED
    at, what is left, or what is over - "what is our budget for
    Medicine/Drugs?". A limit is a number the user chose and it lives
    nowhere else: `ledger` shows what was SPENT, which is a different
    question and cannot answer this one. ``period_month`` is YYYYMM for
    an earlier month; omitted means the current period.

    Only 'limits' carry a real spend-vs-limit status. A commitment's
    'limit' is just what that bill typically costs, so never report one
    as a budget the user set, or as being over or under.
    """
    from app.services.finance.domains.planning.budgets.summary import budget_summary

    async with get_async_session() as session:
        summary = await budget_summary(
            session, owner_user_id=None, period_month=period_month
        )

    def line(row: Any) -> dict[str, Any]:
        return {
            "category": row.category_name,
            "payee": row.payee_label,
            "limit": row.allocated_amount,
            "spent": row.spent_amount,
            "remaining": row.allocated_amount - row.spent_amount,
            "status": row.status,
        }

    limits = [
        line(row)
        for bucket in summary.buckets
        if bucket.name == "flexible"
        for row in bucket.lines
    ]
    commitments = [
        line(row)
        for bucket in summary.buckets
        if bucket.name != "flexible"
        for row in bucket.lines
    ]
    stats = summary.stats
    return {
        "period_month": summary.period_month,
        "limits": limits,
        "commitments": commitments,
        "stats": {
            "flexible_spent": stats.flexible_spent,
            "flexible_allocated": stats.flexible_allocated,
            "days_left_in_period": stats.days_left_in_period,
            "over_budget_count": stats.over_budget_count,
            "over_budget_labels": stats.over_budget_labels,
            "fixed_total": stats.fixed_total,
        },
    }


register_tool(
    "ledger",
    ledger,
    description="Cash flow: monthly rollups or individual transactions",
    replace=True,
)
register_tool(
    "transactions",
    transactions,
    description="Find particular transactions by payee, amount or date",
    replace=True,
)
register_tool(
    "projection",
    projection,
    description="Cash walked forward through scheduled bills over any window",
    replace=True,
)
register_tool(
    "quote",
    quote,
    description="Latest stored closing price for a ticker",
    replace=True,
)
register_tool(
    "budget",
    budget,
    description="The limits the user set, and spend against them this period",
    replace=True,
)

# The balance sheet and the write-side tools live in their own modules;
# importing them here keeps "import ai_tools" the one line that
# registers the whole finance tool surface.
from app.services.finance.ai_account_tools import accounts  # noqa: E402,F401
from app.services.finance.ai_write_tools import (  # noqa: E402,F401
    bill_candidates,
    bills,
    categories,
    pending,
    propose,
    propose_many,
    tags,
    withdraw,
    withdraw_batch,
)
