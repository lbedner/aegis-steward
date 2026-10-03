"""A payee line carves its spending out of its category's line (#288).

Starbucks has its own $400 line; Starbucks is also Eating Out. Its
coffee counted against both, so the month's limits counted each dollar
twice. The category line's figure now leaves out what a payee line in
the same plan already counts, everywhere a line's spend is read: the
month, the drill-down, the edit's answer, the projection, and the months
behind (history and rollover).
"""

from datetime import date

import pytest

from app.services.finance.service import FinanceService
from app.services.finance.utils import transaction_payee_key
from tests.services._finance_factories import (
    category_id,
    seed_account,
    seed_limit,
    seed_txn,
)

# Seeding a plan answers each limit with its own read-back.
pytestmark = pytest.mark.queryspy(threshold=5)

SEPTEMBER, OCTOBER = 202609, 202610
TODAY = date(2026, 10, 15)
STARBUCKS = transaction_payee_key(None, None, "STARBUCKS")


async def _plan(svc: FinanceService, period: int) -> tuple[int, int, int]:
    """Eating Out at $400 and Starbucks at $400, in ``period``; returns the
    category id and the two line ids."""
    eating_out = await category_id(svc, "Food:Restaurants")
    category_line = await seed_limit(svc, eating_out, 40_000, period_month=period)
    payee_line = await svc.upsert_budget_line(
        owner_user_id=1,
        period_month=period,
        category_id=None,
        payee_key=STARBUCKS,
        payee_label="Starbucks",
        allocated_amount=40_000,
    )
    assert category_line.id is not None and payee_line.id is not None
    return eating_out, category_line.id, payee_line.id


async def _spend(svc: FinanceService, eating_out: int, day: date) -> None:
    """$100 at Starbucks and $50 at Chipotle, both Eating Out."""
    account = await seed_account(svc)
    await seed_txn(
        svc, account.id, -10_000, day, name="STARBUCKS", category_id=eating_out
    )
    await seed_txn(
        svc, account.id, -5_000, day, name="CHIPOTLE", category_id=eating_out
    )


class TestTheMonth:
    @pytest.mark.asyncio
    async def test_each_dollar_counts_once(self, svc: FinanceService) -> None:
        eating_out, category_line, payee_line = await _plan(svc, OCTOBER)
        await _spend(svc, eating_out, date(2026, 10, 5))

        summary = await svc.budget_summary(
            owner_user_id=1, period_month=OCTOBER, today=TODAY
        )
        spent = {
            line.id: line.spent_amount for line in summary.bucket("flexible").lines
        }

        assert (spent[category_line], spent[payee_line]) == (5_000, 10_000)

    @pytest.mark.asyncio
    async def test_the_drill_down_shows_what_the_line_counts(
        self, svc: FinanceService
    ) -> None:
        eating_out, category_line, _payee_line = await _plan(svc, OCTOBER)
        await _spend(svc, eating_out, date(2026, 10, 5))

        rows = await svc.budget_line_transactions(
            line_id=category_line, owner_user_id=1, period_month=OCTOBER
        )

        assert [row.name for row in rows] == ["CHIPOTLE"]

    @pytest.mark.asyncio
    async def test_an_edit_answers_with_the_same_figure(
        self, svc: FinanceService
    ) -> None:
        eating_out, _category_line, _payee_line = await _plan(svc, OCTOBER)
        await _spend(svc, eating_out, date(2026, 10, 5))

        edited = await seed_limit(svc, eating_out, 45_000, period_month=OCTOBER)

        assert edited.spent_amount == 5_000


class TestTheMonthsBehind:
    @pytest.mark.asyncio
    async def test_history_carves_each_month_by_its_own_plan(
        self, svc: FinanceService
    ) -> None:
        eating_out, _category_line, _payee_line = await _plan(svc, SEPTEMBER)
        october = await seed_limit(svc, eating_out, 40_000, period_month=OCTOBER)
        await _spend(svc, eating_out, date(2026, 9, 5))

        history = await svc.budget_history(
            owner_user_id=1, months=1, period_month=OCTOBER
        )
        item = next(item for item in history.items if item.line_id == october.id)

        assert [m.spent_amount for m in item.months] == [5_000]
