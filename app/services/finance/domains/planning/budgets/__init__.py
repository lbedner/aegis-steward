"""Budgets: the limits you set, and whether the month survives them.

One concern per module - ``lines`` for setting a limit, ``months`` for
reading a line across the months behind this one, ``suggestions``
for proposing and declining them, ``summary`` for reading a period back,
``trims`` for closing a negative month, ``details`` for the rows behind
one stat, ``outlook`` for running the same equation forward, ``queries``
for the reads only this domain issues.
The package boundary is the API: callers reach every verb as
``budgets.foo(db, ...)`` and never import a submodule.
"""

from app.services.finance.domains.planning.budgets import (
    details,
    lines,
    months,
    outlook,
    queries,
    strip,
    suggestions,
    summary,
    trims,
)
from app.services.finance.domains.planning.budgets.details import (
    budget_stat_details,
)
from app.services.finance.domains.planning.budgets.drilldown import (
    budget_line_transactions,
)
from app.services.finance.domains.planning.budgets.lines import (
    budget_line_status,
    carried_amounts,
    delete_budget_line,
    get_or_create_budget,
    line_in_force,
    lines_in_force,
    remove_budget_line,
    spend_by_line,
    upsert_budget_line,
)
from app.services.finance.domains.planning.budgets.months import line_history
from app.services.finance.domains.planning.budgets.outlook import (
    budget_month_outlook,
    parse_budget_goal,
)
from app.services.finance.domains.planning.budgets.queries import month_bounds
from app.services.finance.domains.planning.budgets.suggestions import (
    _BUDGET_LOOKBACK_MONTHS,
    dismiss_budget_suggestions,
    list_dismissed_suggestions,
    restore_budget_suggestions,
    suggest_budget_lines,
)
from app.services.finance.domains.planning.budgets.summary import (
    budget_summary,
    month_actuals,
)
from app.services.finance.domains.planning.budgets.trims import plan_budget_trims
from app.services.finance.domains.planning.budgets.uncovered import (
    uncovered_spend,
    uncovered_spending_rate,
)

__all__ = [
    "carried_amounts",
    "month_actuals",
    "strip",
    "budget_line_transactions",
    "_BUDGET_LOOKBACK_MONTHS",
    "budget_line_status",
    "budget_month_outlook",
    "budget_stat_details",
    "details",
    "budget_summary",
    "delete_budget_line",
    "line_history",
    "line_in_force",
    "remove_budget_line",
    "dismiss_budget_suggestions",
    "get_or_create_budget",
    "lines_in_force",
    "lines",
    "list_dismissed_suggestions",
    "month_bounds",
    "months",
    "outlook",
    "parse_budget_goal",
    "plan_budget_trims",
    "queries",
    "restore_budget_suggestions",
    "spend_by_line",
    "suggest_budget_lines",
    "suggestions",
    "summary",
    "trims",
    "uncovered_spend",
    "uncovered_spending_rate",
    "upsert_budget_line",
]
