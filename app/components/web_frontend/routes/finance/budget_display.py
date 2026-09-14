"""How a budget READS: month labels, the shapes a card is drawn from,
the words a goal's progress is said in.

Split out of ``budget.py`` at the 500-line budget. Everything here is
pure - it takes rows and returns what a template renders - so it can be
read, and tested, without a database in the room.
"""

from __future__ import annotations

import calendar
from typing import Any

from app.components.web_frontend.filters import money
from app.components.web_frontend.nav import section
from app.services.finance.schemas import (
    BudgetStatsResponse,
    GoalResponse,
)

SECTION = section("budget")

# The choice vocabularies a goal and an envelope are offered, read by
# every form that draws one.
TARGET_RULES = [
    {"id": "fixed", "name": "A fixed amount"},
    {"id": "months_of_expenses", "name": "Months of expenses"},
]
CONTRIBUTION_KINDS = [
    {"id": "fixed", "name": "A fixed amount each month"},
    {"id": "percent_income", "name": "A percent of income"},
    {"id": "surplus", "name": "Whatever the month leaves over"},
]
CADENCES = [{"id": "weekly", "name": "Weekly"}, {"id": "monthly", "name": "Monthly"}]


# --- pure presentation ----------------------------------------------------


def month_label(period_month: int) -> str:
    """``202610`` -> ``October 2026``."""
    return f"{calendar.month_name[period_month % 100]} {period_month // 100}"


def _cell(
    key: str, label: str, value: int, caption: str, tone: str | None = None
) -> dict:
    return {
        "key": key,
        "label": label,
        "value": value,
        "caption": caption,
        "tone": tone,
    }


def stats_cells(stats: BudgetStatsResponse) -> list[dict[str, Any]]:
    """The header strip for the current month: what comes in, what the
    bills take, what the limits take, and the signed remainder. Colour
    only for a month in trouble."""
    plural = "s" if stats.income_count != 1 else ""
    budgets_caption = f"{stats.flexible_count} limits"
    if stats.goals_total > 0:
        budgets_caption += " · + goals"
    cells = [
        _cell(
            "income",
            "Income",
            stats.income_total,
            f"{stats.income_count} confirmed source{plural} / month",
        ),
        _cell(
            "bills", "Bills", stats.fixed_total, f"{stats.fixed_count} bills / month"
        ),
        _cell("budgets", "Budgets", stats.flexible_allocated, budgets_caption),
    ]
    if stats.everything_else > 0:
        cells.append(
            _cell(
                "everything",
                "Everything else",
                stats.everything_else,
                "observed · not in bills or limits",
            )
        )
    if stats.month_net >= 0:
        caption, tone = "Left over at these settings", "ok"
    else:
        caption, tone = (
            f"Short this month · {stats.days_left_in_period} days left",
            "error",
        )
    cells.append(_cell("month", "This month", stats.month_net, caption, tone))
    return cells


def outlook_cells(entry: Any) -> list[dict[str, Any]]:
    """The strip for a FUTURE month: bills at face value on their real
    cadence, and the verdict titled with the month itself."""
    cells = [
        _cell("income", "Income", entry.income_due, "due that month"),
        _cell("bills", "Bills", entry.bills_due, "landing that month, face value"),
        _cell("budgets", "Budgets", entry.budgets, "standing limits"),
    ]
    if entry.everything_else > 0:
        cells.append(
            _cell(
                "everything",
                "Everything else",
                entry.everything_else,
                "observed · not in bills or limits",
            )
        )
    cells.append(
        _cell(
            "month",
            month_label(entry.period_month),
            entry.month_net,
            "at these settings",
            "ok" if entry.month_net >= 0 else "error",
        )
    )
    return cells


def equation_rows(stats: BudgetStatsResponse) -> list[dict[str, Any]]:
    """The verdict as its own arithmetic, from the same stats the strip
    renders; zero terms stay out."""
    rows = [
        {"label": "Income", "value": stats.income_total},
        {"label": "Bills", "value": -stats.fixed_total},
        {"label": "Budgets", "value": -stats.flexible_allocated},
    ]
    for label, amount in (
        ("Goals", stats.goals_total),
        ("Envelopes", stats.envelopes_total),
        ("Everything else", stats.everything_else),
    ):
        if amount:
            rows.append({"label": label, "value": -amount})
    rows.append({"label": "This month", "value": stats.month_net})
    return rows


def eta_caption(goal: GoalResponse) -> str:
    if goal.status == "reached" or goal.progress >= 1:
        return "Reached"
    if goal.status == "paused":
        return "Paused"
    monthly = money(goal.monthly_need)
    if goal.contribution_kind == "percent_income":
        monthly += f" ({(goal.contribution_pct_bps or 0) / 100:g}% of income)"
    elif goal.contribution_kind == "surplus":
        monthly += " (surplus)"
    if goal.eta is None:
        return f"{monthly}/mo · at this rate: never"
    return f"{monthly}/mo · lands {goal.eta:%b %d, %Y}"
