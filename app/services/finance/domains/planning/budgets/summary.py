"""Reading a period back: where the money went, and whether it adds up.

The month equation lives here - income minus bills minus budgets minus
goals minus envelopes minus uncovered spending - along with the per-cell
backup the popups show and the deterministic cuts that close a negative
month. ``outlook`` runs this same equation forward over future months.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Literal

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.insights.commitments import (
    commitment_rollup,
    monthly_share,
)
from app.services.finance.domains.ledger import categories
from app.services.finance.domains.planning import envelopes, goals, recurring
from app.services.finance.domains.planning.budgets import queries, strip
from app.services.finance.domains.planning.budgets.lines import (
    carried_amounts,
    line_response,
    lines_in_force,
)
from app.services.finance.domains.planning.budgets.months import line_spent
from app.services.finance.domains.planning.budgets.trims import plan_budget_trims
from app.services.finance.domains.planning.budgets.uncovered import (
    uncovered_spend,
)
from app.services.finance.models import (
    FinanceBudgetCategory,
    FinanceRecurringStream,
)
from app.services.finance.schemas import (
    BudgetBucketResponse,
    BudgetLineResponse,
    BudgetMonthActuals,
    BudgetStatsResponse,
    BudgetSummaryResponse,
    GoalAsk,
)
from app.services.finance.utils import (
    current_date,
    current_period_month,
    monthly_income,
    shift_period,
    transaction_payee_key,
)


def _commitment_variance_status(
    actual: int, prior: int | None
) -> tuple[Literal["good", "warn"], int | None]:
    """A Fixed/Non-monthly line reads variance against what it cost LAST
    period, not against a limit - it isn't one. Never "critical": a bill
    can't be over budget on itself, only worth a second look if it moved.
    Nothing to compare yet (no prior-period charge, or this period hasn't
    posted) reads as "good"/on schedule rather than a false swing."""
    if not actual or prior is None:
        return "good", None
    variance = actual - prior
    tolerance = max(200, round(prior * 0.02))  # $2 floor or 2%, whichever's bigger
    return ("warn" if abs(variance) > tolerance else "good"), variance


async def budget_summary(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    period_month: int | None = None,
    account_ids: list[int] | None = None,
    today: date | None = None,
) -> BudgetSummaryResponse:
    """Flexible: explicit limits the owner chose to track (category or
    payee) - the only bucket with a real spend-vs-allocation status.
    Fixed/Non-monthly: recurring commitments shown for CONTEXT only
    (an earlier version gave every detected bill, including the
    mortgage, its own spend-vs-allocation status - wrong, a bill's own
    cost isn't a limit anyone set). These read a variance-vs-last-month
    signal instead and never go "critical".

    Query count does NOT grow with the number of budget lines or
    recurring streams: one fetch of this period's transactions and one
    of last period's (each tallied by category/payee-key/recurring-
    stream in a single Python pass) stand in for what would otherwise
    be a spend lookup per line. Do not "simplify" steps 4/5 below into
    per-line queries - that is exactly the N+1 this was built to avoid.
    """
    today = today or current_date()
    month = period_month or current_period_month(today)
    start, end = queries.month_bounds(month)
    prior_start, prior_end = queries.month_bounds(shift_period(month, -1))

    # 1-2. The budget's explicit lines for this period. An empty month
    # inherits the last one that was set; a month with its own lines is
    # left exactly as it is.
    lines = await lines_in_force(db, owner_user_id=owner_user_id, period_month=month)

    # 3. Category display names, batched.
    names = await categories.category_names(
        db, {line.category_id for line in lines if line.category_id is not None}
    )

    # 4. ONE fetch of THIS period's outflows, tallied by category,
    # payee-key, AND recurring-stream in a single Python pass - this
    # is the line that keeps the whole method O(1) queries regardless
    # of how many budget lines or streams exist.
    txn_rows = await queries.outflow_tuples(
        db,
        owner_user_id=owner_user_id,
        start=start,
        end=end,
        account_ids=account_ids,
    )
    spent_by_category: dict[int, int] = defaultdict(int)
    spent_by_payee: dict[str, int] = defaultdict(int)
    spent_by_stream: dict[int, int] = defaultdict(int)
    for (
        cat_id,
        merchant_name,
        original_description,
        name,
        amount,
        stream_id,
    ) in txn_rows:
        spend = -amount
        if cat_id is not None:
            spent_by_category[cat_id] += spend
        key = transaction_payee_key(merchant_name, original_description, name)
        if key:
            spent_by_payee[key] += spend
        if stream_id is not None:
            spent_by_stream[stream_id] += spend

    # 5. ONE fetch of LAST period's per-stream spend - the "vs last
    # month" variance signal on Fixed/Non-monthly, a second FIXED
    # query, not one per stream.
    prior_rows = await queries.outflow_tuples(
        db,
        owner_user_id=owner_user_id,
        start=prior_start,
        end=prior_end,
        account_ids=account_ids,
    )
    spent_by_stream_prior: dict[int, int] = defaultdict(int)
    for *_ignored, amount, stream_id in prior_rows:
        if stream_id is not None:
            spent_by_stream_prior[stream_id] += -amount

    # 6. Recurring commitments (existing detection, ~3 fixed queries),
    # reused rather than re-derived - same source /recurring reads.
    book = await recurring.stream_book(db, owner_user_id=owner_user_id)
    streams = book.counted(account_ids)
    rollup = commitment_rollup(streams, today=today)
    stream_category_names = await recurring.stream_category_names(
        db,
        {s.id for s in rollup["fixed"] + rollup["non_monthly"] + rollup["one_time"]},
    )

    def commitment_line(stream: FinanceRecurringStream) -> BudgetLineResponse:
        # The monthly share: these render under a "/mo" heading.
        typical = monthly_share(stream)
        actual = spent_by_stream.get(stream.id, 0)
        status, variance = _commitment_variance_status(
            actual, spent_by_stream_prior.get(stream.id)
        )
        return BudgetLineResponse(
            id=stream.id,
            category_id=stream.category_id,
            # Named by its category, so one with none says so rather than
            # reading as the overall limit.
            category_name=stream_category_names.get(stream.id, "Uncategorized"),
            payee_key=None,
            payee_label=None,
            allocated_amount=typical,
            spent_amount=actual,
            status=status,
            variance_amount=variance,
        )

    # What the limits that roll over carried in (#360): household-wide,
    # whatever the account filter, like the limits themselves.
    carried = await carried_amounts(
        db, owner_user_id=owner_user_id, period_month=month, lines=lines
    )

    def user_line(line: FinanceBudgetCategory) -> BudgetLineResponse:
        return line_response(
            line,
            names.get(line.category_id) if line.category_id is not None else None,
            line_spent(line, spent_by_category, spent_by_payee),
            carried.get(line.id or 0, 0),
        )

    def bucket(
        name: Literal["fixed", "non_monthly", "one_time", "flexible"],
        item_lines: list[BudgetLineResponse],
    ) -> BudgetBucketResponse:
        return BudgetBucketResponse(
            name=name,
            total_allocated=sum(row.allocated_amount for row in item_lines),
            total_spent=sum(row.spent_amount for row in item_lines),
            lines=item_lines,
        )

    def one_time_line(stream: FinanceRecurringStream) -> BudgetLineResponse:
        # Face value beside a date, never a "/mo" share: a one-off is a
        # plan the user typed in, and its whole amount lands on one day.
        return BudgetLineResponse(
            id=stream.id,
            category_id=stream.category_id,
            category_name=stream_category_names.get(stream.id),
            payee_key=None,
            payee_label=stream.name,
            allocated_amount=int(stream.average_amount or 0),
            spent_amount=spent_by_stream.get(stream.id, 0),
            status="good",
            variance_amount=None,
            due_date=stream.next_expected_date,
        )

    fixed_lines = [commitment_line(s) for s in rollup["fixed"]]
    non_monthly_lines = [commitment_line(s) for s in rollup["non_monthly"]]
    one_time_lines = sorted(
        (one_time_line(s) for s in rollup["one_time"]),
        key=lambda row: (row.due_date is None, row.due_date or date.min),
    )
    flexible_lines = [user_line(line) for line in lines]

    # 7. Stats strip - derived entirely from data already in memory
    # above, no further queries.
    over_budget = [row for row in flexible_lines if row.status == "critical"]
    days_left = (end - today).days if start <= today < end else 0
    flexible_spent = sum(row.spent_amount for row in flexible_lines)
    flexible_allocated = sum(row.allocated_amount for row in flexible_lines)
    # MONTHLY-EQUIVALENT, not the sum of face values: this is the
    # header's "Bills / month" figure AND the number ``month_net``
    # subtracts below, so the two must ride the same footing. A
    # quarterly $300 bill costs $100 a month; summing the face
    # values instead (the original) overstated the cell by the
    # whole non-monthly book, and the strip visibly failed its own
    # arithmetic - the three cells on display did not subtract to
    # the fourth.
    fixed_total = rollup["monthly_total"]

    # 8. The month's bottom line: confirmed income minus confirmed
    # bills minus budget allocations, all monthly-equivalent - the
    # same commitment gate and factors the forecast walks with, so
    # this verdict and the Projected tab cannot disagree. Budget
    # lines are the flexible ones only; a category a bill covers is
    # already excluded from budgets by the suggestion guards.

    income_total, income_count = monthly_income(streams, today)
    # Goals ask their monthly need of the month, the same
    # commitment-gate discipline bills ride:
    # paused/reached goals ask nothing, by the pure-math contract.
    # Local: the engine reaches back into budgets, so they meet at call time.
    from app.services.finance.domains.planning import allocation

    goal_accounts = await goals.list_goals(db, owner_user_id=owner_user_id)
    # The household's figures, the ones the Goals tab and the outlook
    # ask against: a goal is the household's plan, whatever the filter.
    figures = await allocation.figures_for(
        db,
        book,
        allocated=flexible_allocated,
        owner_user_id=owner_user_id,
        today=today,
    )
    asks = allocation.asks_by_account(goal_accounts, figures, today=today)
    goal_asks = [
        GoalAsk(
            account_id=account.id,
            label=account.name,
            monthly_need=asks.get(account.id, 0),
        )
        for account in goal_accounts
        if goals.goal_metadata(account.metadata_) is not None
    ]
    goals_total = sum(g.monthly_need for g in goal_asks)
    goals_shortfall = allocation.goal_shortfall(
        figures, {str(g.account_id): g.monthly_need for g in goal_asks}
    )

    # Auto-credit envelopes are spoken-for money too.
    envelope_credits = [
        meta.monthly_amount
        for _account, meta in await envelopes.auto_credited(
            db, owner_user_id=owner_user_id
        )
    ]
    envelopes_total = sum(envelope_credits)

    # The sixth term: observed spending no bill and no limit covers.
    uncovered = await uncovered_spend(
        db, owner_user_id=owner_user_id, today=today, account_ids=account_ids
    )

    # Subtracts the figures the header STRIP shows, not equivalents of
    # them recomputed here - the cells and this verdict are one
    # arithmetic statement, and a reader checking it by hand has to
    # get the same answer.
    month_net = strip.month_net(
        income=income_total,
        bills=fixed_total,
        budgets=flexible_allocated,
        goals=goals_total,
        envelopes=envelopes_total,
        everything_else=uncovered.rate,
    )

    # 9. When the month lands negative, the summary carries its own
    # fix: pause-a-goal rows first, then deterministic per-line cuts
    # (see plan_budget_trims). One payload, so the tab offers the
    # adjustment beside the verdict and a later decision layer reads
    # the same structure.
    plan = plan_budget_trims(
        flexible_lines,
        deficit=max(0, -month_net),
        goals=goal_asks,
    )

    stats = BudgetStatsResponse(
        flexible_spent=flexible_spent,
        flexible_allocated=flexible_allocated,
        days_left_in_period=max(days_left, 0),
        flexible_count=len(flexible_lines),
        on_track_count=len(flexible_lines) - len(over_budget),
        over_budget_count=len(over_budget),
        over_budget_labels=[row.label for row in over_budget],
        fixed_total=fixed_total,
        fixed_count=len(fixed_lines) + len(non_monthly_lines),
        income_total=income_total,
        income_count=income_count,
        goals_total=goals_total,
        goals_count=sum(1 for g in goal_asks if g.monthly_need > 0),
        goals_shortfall=goals_shortfall,
        envelopes_total=envelopes_total,
        envelopes_count=len(envelope_credits),
        everything_else=uncovered.rate,
        one_off_total=uncovered.one_off,
        month_net=month_net,
        trim_residual=plan.residual,
    )

    return BudgetSummaryResponse(
        period_month=month,
        buckets=[
            bucket("fixed", fixed_lines),
            bucket("non_monthly", non_monthly_lines),
            bucket("one_time", one_time_lines),
            bucket("flexible", flexible_lines),
        ],
        stats=stats,
        trims=plan.cuts,
    )


async def month_actuals(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    period_month: int | None = None,
    account_ids: list[int] | None = None,
) -> BudgetMonthActuals:
    """What came in and went out in one month, from its transactions: a
    month that has ended reads as it happened, and nothing in it is
    rebuilt from today's bills (#359)."""
    month = period_month or current_period_month()
    start, end = queries.month_bounds(month)
    money_in, money_out = await queries.money_in_and_out(
        db, owner_user_id=owner_user_id, start=start, end=end, account_ids=account_ids
    )
    return BudgetMonthActuals(
        period_month=month, money_in=money_in, money_out=money_out
    )
