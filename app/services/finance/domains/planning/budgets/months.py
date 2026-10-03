"""A line across the months behind this one.

Two readers walk back through ended months: the carry a rolling-over
limit brings in (#360, ``lines.carried_amounts``) and a line's history
(#345, ``line_history``). Both need the same two answers - which plan a
month ran on, and what each target spent that month - so both live here
once.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.planning.budgets import queries
from app.services.finance.models import FinanceBudgetCategory, FinanceTransaction
from app.services.finance.schemas import BudgetLineHistory, LineMonth
from app.services.finance.utils import (
    current_period_month,
    shift_period,
    transaction_payee_key,
)

# What a line limits: a category, a payee key, or neither (overall).
Target = tuple[int | None, str | None]


@dataclass
class Tally:
    """One period's spend: by category id, by payee key, and by the two
    together - the last is what a payee line carves out of its category's
    line (#288)."""

    by_category: dict[int, int] = field(default_factory=dict)
    by_payee: dict[str, int] = field(default_factory=dict)
    by_category_payee: dict[tuple[int, str], int] = field(default_factory=dict)

    def add(
        self,
        cat_id: int | None,
        merchant: str | None,
        description: str | None,
        name: str | None,
        amount: int,
    ) -> None:
        """One ``outflow_tuples`` row; outflows are stored negative."""
        spend = -amount
        key = transaction_payee_key(merchant, description, name)
        if cat_id is not None:
            self.by_category[cat_id] = self.by_category.get(cat_id, 0) + spend
        if key:
            self.by_payee[key] = self.by_payee.get(key, 0) + spend
        if cat_id is not None and key:
            pair = (cat_id, key)
            self.by_category_payee[pair] = self.by_category_payee.get(pair, 0) + spend


def live(lines: list[FinanceBudgetCategory]) -> list[FinanceBudgetCategory]:
    """Without the removed ones, which only mark a month as decided."""
    return [line for line in lines if line.deleted_at is None]


def target(line: FinanceBudgetCategory) -> Target:
    return line.category_id, line.payee_key


def carved_out(plan: Iterable[FinanceBudgetCategory]) -> frozenset[str]:
    """The payee keys a plan limits on their own: their spending is theirs,
    not their category line's too (#288)."""
    return frozenset(
        line.payee_key
        for line in plan
        if line.category_id is None and line.payee_key and line.deleted_at is None
    )


def line_spent(
    line: FinanceBudgetCategory,
    tally: Tally,
    carved: AbstractSet[str] = frozenset(),
) -> int:
    """A line's spend out of a period's tally: its category's, less what
    the plan's payee lines (``carved``) already count, else its payee's.
    The overall line tracks neither. Each dollar counts once."""
    if line.category_id is not None:
        return tally.by_category.get(line.category_id, 0) - sum(
            tally.by_category_payee.get((line.category_id, key), 0) for key in carved
        )
    return tally.by_payee.get(line.payee_key, 0) if line.payee_key else 0


class PastPlans:
    """The limits each earlier month ran on. A month nobody opened ran on
    the latest earlier month that had limits, as ``lines_in_force`` reads
    it; a month before any limits ran on none."""

    def __init__(self, rows: Sequence[FinanceBudgetCategory]) -> None:
        self._by_period: dict[int, list[FinanceBudgetCategory]] = {}
        for row in rows:
            self._by_period.setdefault(row.period_month or 0, []).append(row)
        self._periods = sorted(self._by_period)

    @property
    def first(self) -> int | None:
        return self._periods[0] if self._periods else None

    def in_force(self, month: int) -> dict[Target, FinanceBudgetCategory]:
        at = bisect_right(self._periods, month) - 1
        if at < 0:
            return {}
        return {target(row): row for row in live(self._by_period[self._periods[at]])}


async def past_plans(db: AsyncSession, budget_id: int, before_period: int) -> PastPlans:
    return PastPlans(await queries.budget_lines_before(db, budget_id, before_period))


async def spend_by_month(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    first: int,
    before: int,
    account_ids: list[int] | None = None,
) -> dict[int, Tally]:
    """Each month's tally from ``first`` up to ``before`` (YYYYMM), in one
    fetch."""
    start, _ = queries.month_bounds(first)
    end, _ = queries.month_bounds(before)
    tallies: dict[int, Tally] = {}
    for (
        cat_id,
        merchant,
        description,
        name,
        amount,
        _stream,
        day,
    ) in await queries.outflow_tuples(
        db,
        owner_user_id=owner_user_id,
        start=start,
        end=end,
        account_ids=account_ids,
        extra=(FinanceTransaction.date_,),
    ):
        tallies.setdefault(current_period_month(day), Tally()).add(
            cat_id, merchant, description, name, amount
        )
    return tallies


async def line_history(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    period_month: int,
    months: int,
    lines: Sequence[FinanceBudgetCategory],
    account_ids: list[int] | None = None,
) -> list[BudgetLineHistory]:
    """Each of ``lines`` (``period_month``'s) across the ``months`` ended
    months before it, oldest first: what it spent, and the limit that
    month ran on for the same target. Three queries, however many lines
    and months."""
    if not lines:
        return []
    window = [shift_period(period_month, -back) for back in range(months, 0, -1)]
    plans = await past_plans(db, lines[0].budget_id, period_month)
    spend = await spend_by_month(
        db,
        owner_user_id=owner_user_id,
        first=window[0],
        before=period_month,
        account_ids=account_ids,
    )
    in_force = {month: plans.in_force(month) for month in window}
    # Each month carves by the plan it ran on; a month before any plan by
    # today's, so its bar reads the way today's line does.
    carved = {month: carved_out(in_force[month].values() or lines) for month in window}
    return [
        BudgetLineHistory(
            line_id=line.id,
            months=[
                LineMonth(
                    period_month=month,
                    spent_amount=line_spent(
                        line, spend.get(month, Tally()), carved[month]
                    ),
                    allocated_amount=(
                        ran.allocated_amount
                        if (ran := in_force[month].get(target(line)))
                        else None
                    ),
                )
                for month in window
            ],
        )
        for line in lines
        if line.id is not None
    ]
