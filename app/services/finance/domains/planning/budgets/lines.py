"""The budget itself, and the individual limits set against it.

One standing budget row per owner; the month-by-month allocations are
lines hanging off it. Everything here is about setting or clearing one
limit and answering for it immediately - reading the whole period back
is ``summary``, proposing new lines is ``suggestions``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.services.finance.domains.ledger import accounts, categories
from app.services.finance.domains.planning import queries as planning_queries
from app.services.finance.domains.planning.budgets import queries
from app.services.finance.domains.planning.budgets.months import (
    Tallies,
    Target,
    line_spent,
    live,
    past_plans,
    spend_by_month,
    target,
)
from app.services.finance.models import (
    FinanceBudget,
    FinanceBudgetCategory,
)
from app.services.finance.schemas import BudgetLineResponse
from app.services.finance.utils import (
    DEFAULT_CURRENCY,
    current_period_month,
    shift_period,
)


def budget_line_status(
    allocated_amount: int, spent_amount: int
) -> Literal["good", "warn", "critical"]:
    """good / warn / critical - budgets warn at 80% spent, not the 70% a
    resource-utilization card would use (see backend_modal's CPU/Memory
    thresholds - a different domain, not reused here on purpose)."""
    if allocated_amount <= 0:
        return "critical" if spent_amount > 0 else "good"
    pct = spent_amount / allocated_amount
    if pct >= 1.0:
        return "critical"
    if pct >= 0.8:
        return "warn"
    return "good"


async def get_or_create_budget(
    db: AsyncSession, *, owner_user_id: int | None, period_month: int
) -> FinanceBudget:
    """The owner's one standing "Monthly" budget - created on first use,
    reused after (one SELECT, one INSERT only the first time ever).

    ``period_month`` only seeds ``start_date`` on that first creation;
    every later month reuses this same row. Each month's actual
    allocations live on ``FinanceBudgetCategory``, keyed by
    ``period_month`` - the budget definition itself doesn't repeat.
    """
    existing = await queries.monthly_budget(db, owner_user_id=owner_user_id)
    if existing is not None:
        return existing
    await accounts.get_or_create_currency(db, DEFAULT_CURRENCY)
    start, _ = queries.month_bounds(period_month)
    budget = FinanceBudget(
        # NOT NULL column - standalone (no-auth) installs use the same
        # ``0`` owner sentinel ``create_recurring_stream`` already does.
        owner_user_id=0 if owner_user_id is None else owner_user_id,
        name="Monthly",
        period="monthly",
        start_date=start,
    )
    db.add(budget)
    await db.flush()
    return budget


async def lines_in_force(
    db: AsyncSession, *, owner_user_id: int | None, period_month: int
) -> list[FinanceBudgetCategory]:
    """The plan the owner's month runs on, inheriting the last one if empty.

    A budget is a standing decision, not a monthly chore: allocations are
    keyed by period, so without this every month opened as "everything
    else" and the only way back was to type it all in again.

    The ONLY read of a period's lines. Every surface that asks - the
    budget page, the projection, the month strip, the uncovered-spend
    rate - has to get the same answer, and while the summary was the
    only caller that inherited, the same month read differently
    depending on which tab was opened first.

    Amounts only. Spend is computed from this period's transactions, and
    a copied ``spent_amount`` would read as money already gone. A period
    with any line of its own, even one since removed, is a month someone
    has decided about, and is never touched (#320).
    """
    budget = await get_or_create_budget(
        db, owner_user_id=owner_user_id, period_month=period_month
    )
    return live(await _period_rows(db, budget_id=budget.id, period_month=period_month))


async def _period_rows(
    db: AsyncSession, *, budget_id: int, period_month: int
) -> list[FinanceBudgetCategory]:
    """Every row of the period, removed lines included, after seeding an
    empty period from the latest one that has rows."""
    rows = await queries.budget_lines_for_period(db, budget_id, period_month)
    if rows:
        return rows

    source = await queries.latest_period_with_lines(db, budget_id, period_month)
    if source is None:
        return []

    copied = [
        FinanceBudgetCategory(
            owner_user_id=line.owner_user_id,
            budget_id=budget_id,
            period_month=period_month,
            category_id=line.category_id,
            payee_key=line.payee_key,
            payee_label=line.payee_label,
            allocated_amount=line.allocated_amount,
            rollover_enabled=line.rollover_enabled,
            currency=line.currency,
        )
        for line in live(await queries.budget_lines_for_period(db, budget_id, source))
    ]
    # The dashboard opens several panels at once and every one of them
    # asks this question, so two callers can both find the period empty
    # and both seed it. The partial unique index settles who wins; the
    # loser wants the winner's rows, not a failed request. The savepoint
    # keeps the rejection from poisoning the surrounding transaction.
    try:
        async with db.begin_nested():
            db.add_all(copied)
    except IntegrityError:
        return await queries.budget_lines_for_period(db, budget_id, period_month)
    return copied


async def _own_line(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    month: int,
    category_id: int | None,
    payee_key: str | None,
) -> tuple[FinanceBudget, FinanceBudgetCategory | None]:
    """The budget, and the month's own row for one target (a category, a
    payee key, or the overall line when both are None), removed or not:
    setting a removed limit again brings that row back.

    A month with no lines of its own runs on the last month's, so it is
    seeded first: changing one line of it would otherwise leave it with
    ONLY that line, every inherited limit gone for this month and every
    month copied from it (#265).
    """
    budget = await get_or_create_budget(
        db, owner_user_id=owner_user_id, period_month=month
    )
    rows = await _period_rows(db, budget_id=budget.id, period_month=month)
    line = next(
        (
            row
            for row in rows
            if (row.category_id, row.payee_key) == (category_id, payee_key)
        ),
        None,
    )
    return budget, line


async def line_in_force(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    period_month: int | None,
    category_id: int | None,
    payee_key: str | None,
) -> FinanceBudgetCategory | None:
    """The limit a month runs on for one target, its own or the one it
    inherits; None when the month has no limit for it."""
    _budget, line = await _own_line(
        db,
        owner_user_id=owner_user_id,
        month=period_month or current_period_month(),
        category_id=category_id,
        payee_key=payee_key,
    )
    return line if line is not None and line.deleted_at is None else None


async def spend_by_line(
    db: AsyncSession,
    lines: Sequence[FinanceBudgetCategory],
    *,
    owner_user_id: int | None,
    start: date,
    end: date,
) -> list[int]:
    """Each line's spend over ``[start, end)``, in the order given: three
    queries at most, however many lines. ``budget_summary`` tallies the
    same figures out of its one pass over the month instead."""
    by_category = await planning_queries.spend_by_category(
        db,
        owner_user_id=owner_user_id,
        start=start,
        end=end,
        category_ids={
            line.category_id for line in lines if line.category_id is not None
        },
    )
    by_payee = await planning_queries.spend_by_payee_key(
        db,
        owner_user_id=owner_user_id,
        start=start,
        end=end,
        payee_keys={
            line.payee_key
            for line in lines
            if line.category_id is None and line.payee_key
        },
    )
    return [line_spent(line, by_category, by_payee) for line in lines]


async def carried_amounts(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    period_month: int,
    lines: Sequence[FinanceBudgetCategory],
) -> dict[int, int]:
    """What each rolling-over line carries into ``period_month``, by line id.

    Leftover keeps stacking and overspending carries too (#360): the carry
    is everything the target was allowed and did not spend, less what it
    overspent, over the unbroken run of earlier months that rolled it
    over. A month that ran without the limit, or with rollover off, ends
    the run, so turning it back on starts fresh. A month nobody opened ran
    on the latest earlier month that had limits, as ``lines_in_force``
    reads it. Lines that do not roll over cost no query at all.
    """
    rolling = {target(line): line for line in lines if line.rollover_enabled}
    if not rolling:
        return {}
    plans = await past_plans(db, next(iter(rolling.values())).budget_id, period_month)
    runs: dict[Target, list[tuple[int, FinanceBudgetCategory]]]
    runs = {key: [] for key in rolling}
    open_runs = set(rolling)
    month = shift_period(period_month, -1)
    while open_runs and plans.first is not None and month >= plans.first:
        in_force = plans.in_force(month)
        for key in list(open_runs):
            row = in_force.get(key)
            if row is None or not row.rollover_enabled:
                open_runs.discard(key)
            else:
                runs[key].append((month, row))
        month = shift_period(month, -1)
    months = {month for run in runs.values() for month, _row in run}
    if not months:
        return {line.id: 0 for line in rolling.values() if line.id is not None}
    spend = await spend_by_month(
        db, owner_user_id=owner_user_id, first=min(months), before=period_month
    )
    empty: Tallies = ({}, {})
    return {
        rolling[key].id: sum(
            row.allocated_amount - line_spent(row, *spend.get(month, empty))
            for month, row in run
        )
        for key, run in runs.items()
        if rolling[key].id is not None
    }


def line_response(
    line: FinanceBudgetCategory,
    category_name: str | None,
    spent: int,
    carried: int = 0,
) -> BudgetLineResponse:
    """A stored limit as every surface receives it; a rolling-over one
    judged against what it carried in as well."""
    return BudgetLineResponse(
        id=line.id,
        category_id=line.category_id,
        category_name=category_name,
        payee_key=line.payee_key,
        payee_label=line.payee_label,
        allocated_amount=line.allocated_amount,
        spent_amount=spent,
        status=budget_line_status(line.allocated_amount + carried, spent),
        rollover=line.rollover_enabled,
        carried_amount=carried,
    )


async def upsert_budget_line(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    period_month: int | None,
    category_id: int | None,
    payee_key: str | None,
    payee_label: str | None,
    allocated_amount: int,
    rollover_enabled: bool | None = None,
) -> BudgetLineResponse:
    """Set (create or replace) one budget line for the period. One
    lookup on the matching partial-unique key, one write, plus one
    scoped spend query so the response's status is correct immediately
    (a category with existing spend shouldn't show "good" at 0).
    ``rollover_enabled`` None leaves the line's rollover as it was."""
    month = period_month or current_period_month()
    budget, line = await _own_line(
        db,
        owner_user_id=owner_user_id,
        month=month,
        category_id=category_id,
        payee_key=payee_key,
    )
    if line is None:
        line = FinanceBudgetCategory(
            owner_user_id=0 if owner_user_id is None else owner_user_id,
            budget_id=budget.id,
            category_id=category_id,
            payee_key=payee_key,
            period_month=month,
        )
    line.payee_label = payee_label
    line.allocated_amount = allocated_amount
    if rollover_enabled is not None:
        line.rollover_enabled = rollover_enabled
    # Setting a removed limit again brings its row back.
    line.deleted_at = None
    line.updated_at = utcnow()
    db.add(line)
    await db.flush()

    category_name = None
    if line.category_id is not None:
        names = await categories.category_names(db, {line.category_id})
        category_name = names.get(line.category_id)
    start, end = queries.month_bounds(month)
    [spent] = await spend_by_line(
        db, [line], owner_user_id=owner_user_id, start=start, end=end
    )
    carried = await carried_amounts(
        db, owner_user_id=owner_user_id, period_month=month, lines=[line]
    )
    return line_response(line, category_name, spent, carried.get(line.id or 0, 0))


async def delete_budget_line(
    db: AsyncSession, line_id: int, *, owner_user_id: int | None = None
) -> bool:
    line = await queries.budget_line_by_id(db, line_id, owner_user_id=owner_user_id)
    if line is None:
        return False
    await _remove(db, line)
    return True


async def remove_budget_line(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    period_month: int | None,
    category_id: int | None,
    payee_key: str | None,
) -> int | None:
    """Remove a month's limit for one target, its own or the one it
    inherits: the id removed, or None when there is no such limit."""
    line = await line_in_force(
        db,
        owner_user_id=owner_user_id,
        period_month=period_month,
        category_id=category_id,
        payee_key=payee_key,
    )
    if line is None:
        return None
    await _remove(db, line)
    return line.id


async def _remove(db: AsyncSession, line: FinanceBudgetCategory) -> None:
    """The row stays, marked removed, so its month keeps the decision
    instead of inheriting the month before (#320)."""
    line.deleted_at = line.updated_at = utcnow()
    db.add(line)
    await db.flush()
