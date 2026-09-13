"""Which transactions made up one budget limit's spend.

The Limits tab shows "$535.16 of $1,000.00" and, until now, no way to
ask which transactions that was. The figure comes from one tally over
the period's outflows (see ``budget_summary``), so this answers with the
same window, the same predicate and the same matching rule - a
drill-down that does not add up to the figure it was opened from is
worse than none at all.

Matching is deliberately EXACT, not the parent-prefix rollup the
Overview pie uses: a limit on "Food & Dining:Groceries" is a limit on
that leaf, and its total never included its siblings.
"""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import queries as ledger_queries
from app.services.finance.domains.planning.budgets import queries
from app.services.finance.models import FinanceTransaction
from app.services.finance.utils import current_period_month, transaction_payee_key


async def budget_line_transactions(
    db: AsyncSession,
    *,
    line_id: int,
    owner_user_id: int | None = None,
    period_month: int | None = None,
    account_ids: list[int] | None = None,
) -> list[FinanceTransaction]:
    """The rows behind one limit, newest first ([] when it has none)."""
    line = await queries.budget_line_by_id(db, line_id, owner_user_id=owner_user_id)
    if line is None:
        return []
    start, end = queries.month_bounds(period_month or current_period_month())
    rows = await queries.outflow_rows(
        db,
        owner_user_id=owner_user_id,
        start=start,
        end=end,
        account_ids=account_ids,
    )
    if line.category_id is not None:
        wanted = line.category_id
        splits = await ledger_queries.splits_for_parents(
            db, [r.id for r in rows if r.is_split and r.id is not None]
        )
        return [
            row
            for row in rows
            if (
                row.category_id == wanted
                if not row.is_split
                # A split parent counts when one of its LINES is what the
                # limit is on - the tally counted the line, so the reader
                # is shown the parent it came from.
                else any(s.category_id == wanted for s in splits.get(row.id, ()))
            )
        ]
    key = line.payee_key or ""
    if not key:
        return []
    return [
        row
        for row in rows
        if transaction_payee_key(row.merchant_name, row.original_description, row.name)
        == key
    ]
