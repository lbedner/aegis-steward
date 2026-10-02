"""Budget lines, suggestions, the month summary and the outlook.

One mixin of the ``FinanceService`` facade: every method here forwards
to the matching domain module as ``module.func(self.db, ...)``.
"""

from __future__ import annotations

from datetime import date

from app.services.finance.domains.planning import budgets
from app.services.finance.models import (
    FinanceBudget,
    FinanceTransaction,
)
from app.services.finance.schemas import (
    BudgetHistoryResponse,
    BudgetLineResponse,
    BudgetMonthActuals,
    BudgetMonthOutlook,
    BudgetStatDetailsResponse,
    BudgetSuggestion,
    BudgetSummaryResponse,
    DismissedBudgetSuggestion,
    GoalParseResponse,
)
from app.services.finance.service.base import FinanceServiceBase
from app.services.finance.utils import current_period_month


class BudgetsMixin(FinanceServiceBase):
    """Budget lines, suggestions, the month summary and the outlook."""

    async def get_or_create_budget(
        self, *, owner_user_id: int | None, period_month: int
    ) -> FinanceBudget:
        return await budgets.get_or_create_budget(
            self.db,
            owner_user_id=owner_user_id,
            period_month=period_month,
        )

    async def suggest_budget_lines(
        self,
        *,
        owner_user_id: int | None = None,
        today: date | None = None,
    ) -> list[BudgetSuggestion]:
        return await budgets.suggest_budget_lines(
            self.db,
            owner_user_id=owner_user_id,
            today=today,
        )

    async def list_dismissed_suggestions(
        self, *, owner_user_id: int | None = None
    ) -> list[DismissedBudgetSuggestion]:
        return await budgets.list_dismissed_suggestions(
            self.db,
            owner_user_id=owner_user_id,
        )

    async def dismiss_budget_suggestions(
        self, *, owner_user_id: int | None = None, category_ids: list[int]
    ) -> int:
        return await budgets.dismiss_budget_suggestions(
            self.db,
            owner_user_id=owner_user_id,
            category_ids=category_ids,
        )

    async def restore_budget_suggestions(
        self, *, owner_user_id: int | None = None, category_ids: list[int]
    ) -> int:
        return await budgets.restore_budget_suggestions(
            self.db,
            owner_user_id=owner_user_id,
            category_ids=category_ids,
        )

    async def upsert_budget_line(
        self,
        *,
        owner_user_id: int | None,
        period_month: int | None,
        category_id: int | None,
        payee_key: str | None,
        payee_label: str | None,
        allocated_amount: int,
        rollover_enabled: bool | None = None,
    ) -> BudgetLineResponse:
        return await budgets.upsert_budget_line(
            self.db,
            owner_user_id=owner_user_id,
            period_month=period_month,
            category_id=category_id,
            payee_key=payee_key,
            payee_label=payee_label,
            allocated_amount=allocated_amount,
            rollover_enabled=rollover_enabled,
        )

    async def delete_budget_line(
        self, line_id: int, *, owner_user_id: int | None = None
    ) -> bool:
        return await budgets.delete_budget_line(
            self.db,
            line_id,
            owner_user_id=owner_user_id,
        )

    async def budget_line_transactions(
        self,
        line_id: int,
        *,
        owner_user_id: int | None = None,
        period_month: int | None = None,
        account_ids: list[int] | None = None,
    ) -> list[FinanceTransaction]:
        return await budgets.budget_line_transactions(
            self.db,
            line_id=line_id,
            owner_user_id=owner_user_id,
            period_month=period_month,
            account_ids=account_ids,
        )

    async def budget_summary(
        self,
        *,
        owner_user_id: int | None = None,
        period_month: int | None = None,
        account_ids: list[int] | None = None,
        today: date | None = None,
    ) -> BudgetSummaryResponse:
        return await budgets.budget_summary(
            self.db,
            owner_user_id=owner_user_id,
            period_month=period_month,
            account_ids=account_ids,
            today=today,
        )

    async def budget_month_actuals(
        self,
        *,
        owner_user_id: int | None = None,
        period_month: int | None = None,
        account_ids: list[int] | None = None,
    ) -> BudgetMonthActuals:
        return await budgets.month_actuals(
            self.db,
            owner_user_id=owner_user_id,
            period_month=period_month,
            account_ids=account_ids,
        )

    async def budget_history(
        self,
        *,
        owner_user_id: int | None = None,
        months: int,
        period_month: int | None = None,
        account_ids: list[int] | None = None,
    ) -> BudgetHistoryResponse:
        """Each of the month's limits across the ``months`` that ended
        before it (#345)."""
        period_month = period_month or current_period_month()
        lines = await budgets.lines_in_force(
            self.db, owner_user_id=owner_user_id, period_month=period_month
        )
        items = await budgets.line_history(
            self.db,
            owner_user_id=owner_user_id,
            period_month=period_month,
            months=months,
            lines=lines,
            account_ids=account_ids,
        )
        return BudgetHistoryResponse(months=months, items=items)

    async def uncovered_spending_rate(
        self,
        *,
        owner_user_id: int | None = None,
        today: date | None = None,
        account_ids: list[int] | None = None,
    ) -> int:
        return await budgets.uncovered_spending_rate(
            self.db,
            owner_user_id=owner_user_id,
            today=today,
            account_ids=account_ids,
        )

    async def budget_stat_details(
        self,
        *,
        owner_user_id: int | None = None,
        today: date | None = None,
        account_ids: list[int] | None = None,
    ) -> BudgetStatDetailsResponse:
        return await budgets.budget_stat_details(
            self.db,
            owner_user_id=owner_user_id,
            today=today,
            account_ids=account_ids,
        )

    async def budget_month_outlook(
        self,
        *,
        owner_user_id: int | None = None,
        months: int = 6,
        today: date | None = None,
        account_ids: list[int] | None = None,
    ) -> list[BudgetMonthOutlook]:
        return await budgets.budget_month_outlook(
            self.db,
            owner_user_id=owner_user_id,
            months=months,
            today=today,
            account_ids=account_ids,
        )

    async def parse_budget_goal(
        self, *, owner_user_id: int | None, text: str
    ) -> GoalParseResponse:
        return await budgets.parse_budget_goal(
            self.db,
            owner_user_id=owner_user_id,
            text=text,
        )
