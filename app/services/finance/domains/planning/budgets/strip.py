"""How the month strip reads: its cells, the verdict's arithmetic, the
pager's chips, and what each cell opens.

Both frontends draw what this returns, and neither composes a caption, a
title or a footer of its own. They used to, and the copies drifted: the
web's Budgets cell never said what was spent, its short month never said
how much of it was bills, and its popups lost their captions and window.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from app.core.formatting import format_money
from app.services.finance.schemas import (
    BudgetMonthOutlook,
    BudgetStatDetailsResponse,
    BudgetStatsResponse,
    BudgetSummaryResponse,
    StatDetailRow,
)
from app.services.finance.utils import period_label

# The cells whose popups list the stat details; the other two read the
# summary already on screen.
DETAIL_KEYS = ("income", "bills", "everything")
STAT_KEYS = (*DETAIL_KEYS, "budgets", "month")

# The bills folded under the limits: each bucket's title, and what its
# total means.
COMMITMENT_BUCKETS: tuple[
    tuple[Literal["fixed", "non_monthly", "one_time"], str, str], ...
] = (
    ("fixed", "Monthly bills", "the same every month"),
    ("non_monthly", "Non-monthly bills", "at their monthly share"),
    ("one_time", "One-time", "this month only"),
)


@dataclass(frozen=True)
class Cell:
    """One cell of the strip; ``key`` is the popup it opens."""

    key: str
    label: str
    value: int
    caption: str
    tone: Literal["ok", "error"] | None = None

    @property
    def display(self) -> str:
        """The figure as the cell shows it: a month that clears is signed."""
        return format_money(self.value, signed=self.key == "month")


@dataclass(frozen=True)
class Chip:
    """One month on the pager."""

    label: str
    tone: Literal["error"] | None = None


@dataclass(frozen=True)
class Row:
    """One line of a popup."""

    label: str
    value: int
    caption: str | None = None


@dataclass(frozen=True)
class Popup:
    """What a cell opens."""

    title: str
    rows: list[Row]
    footer: str = ""


def _terms(
    *,
    income: int,
    bills: int,
    budgets: int,
    goals: int,
    envelopes: int,
    everything_else: int,
) -> list[tuple[str, int]]:
    """The verdict's terms, signed, in the order the strip reads them."""
    return [
        ("Income", income),
        ("Bills", -bills),
        ("Budgets", -budgets),
        ("Goals", -goals),
        ("Envelopes", -envelopes),
        ("Everything else", -everything_else),
    ]


def month_net(
    *,
    income: int,
    bills: int,
    budgets: int,
    goals: int,
    envelopes: int,
    everything_else: int,
) -> int:
    """What the month leaves: what comes in, less all it has spoken for."""
    terms = _terms(
        income=income,
        bills=bills,
        budgets=budgets,
        goals=goals,
        envelopes=envelopes,
        everything_else=everything_else,
    )
    return sum(value for _label, value in terms)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _asks(goals: int, envelopes: int) -> str:
    """The asks that ride on the budgets, as a caption's tail."""
    return "".join(
        f" · + {format_money(amount)} to {what}"
        for what, amount in (("goals", goals), ("envelopes", envelopes))
        if amount > 0
    )


def _everything_else(amount: int) -> list[Cell]:
    """The sixth term earns a cell, not a caption, when there is any: it
    was bigger than the budgets figure when it was found."""
    if amount <= 0:
        return []
    return [
        Cell(
            "everything",
            "Everything else",
            amount,
            "observed · not in bills or limits",
        )
    ]


def stats_cells(stats: BudgetStatsResponse) -> list[Cell]:
    """This month: what comes in, what the bills and the limits take, and
    the signed remainder. Colour only says whether the month clears."""
    if stats.month_net >= 0:
        verdict = "Left over at these settings"
    elif stats.trim_residual > 0:
        residual = format_money(stats.trim_residual)
        verdict = f"Short this month · {residual} of it is bills, not budgets"
    else:
        verdict = f"Short this month · {_count(stats.days_left_in_period, 'day')} left"
    spent = format_money(stats.flexible_spent)
    return [
        Cell(
            "income",
            "Income",
            stats.income_total,
            f"{_count(stats.income_count, 'confirmed source')} / month",
        ),
        Cell(
            "bills",
            "Bills",
            stats.fixed_total,
            f"{_count(stats.fixed_count, 'bill')} / month",
        ),
        Cell(
            "budgets",
            "Budgets",
            stats.flexible_allocated,
            f"{spent} spent so far · {_count(stats.flexible_count, 'limit')}"
            + _asks(stats.goals_total, stats.envelopes_total),
        ),
        *_everything_else(stats.everything_else),
        Cell(
            "month",
            "This month",
            stats.month_net,
            verdict,
            "ok" if stats.month_net >= 0 else "error",
        ),
    ]


def outlook_cells(month: BudgetMonthOutlook) -> list[Cell]:
    """A month ahead: bills at face value on their real cadence, and the
    verdict titled with the month, so it is never taken for this one."""
    landing = format_money(month.end_balance)
    return [
        Cell("income", "Income", month.income_due, "due that month"),
        Cell("bills", "Bills", month.bills_due, "landing that month, face value"),
        Cell(
            "budgets",
            "Budgets",
            month.budgets,
            "standing limits" + _asks(month.goals, month.envelopes),
        ),
        *_everything_else(month.everything_else),
        Cell(
            "month",
            period_label(month.period_month),
            month.month_net,
            f"at these settings · ends around {landing}",
            "ok" if month.month_net >= 0 else "error",
        ),
    ]


def commitments_line(summary: BudgetSummaryResponse) -> str:
    """The folded bills in one line: what the month is already committed to."""
    stats = summary.stats
    line = (
        f"{format_money(stats.fixed_total)}/month already committed across "
        f"{_count(stats.fixed_count, 'bill')}"
    )
    one_time = summary.bucket("one_time").total_allocated
    return f"{line}, plus {format_money(one_time)} one-time" if one_time else line


def pager_chips(months: list[BudgetMonthOutlook]) -> list[Chip]:
    """Today's cash, then the cash each month ends with - the level, not
    the rate. Red only where the money runs out."""
    chips = []
    for index, month in enumerate(months):
        if index == 0:
            chips.append(Chip(f"Now {format_money(month.start_balance, whole=True)}"))
            continue
        name = calendar.month_abbr[month.period_month % 100]
        chips.append(
            Chip(
                f"{name} {format_money(month.end_balance, whole=True)}",
                "error" if month.end_balance < 0 else None,
            )
        )
    return chips


def equation(stats: BudgetStatsResponse) -> list[Row]:
    """The verdict as its own arithmetic. Income, bills and budgets always
    show; the other terms only when they ask for something."""
    terms = _terms(
        income=stats.income_total,
        bills=stats.fixed_total,
        budgets=stats.flexible_allocated,
        goals=stats.goals_total,
        envelopes=stats.envelopes_total,
        everything_else=stats.everything_else,
    )
    rows = [
        Row(label, value) for i, (label, value) in enumerate(terms) if i < 3 or value
    ]
    return [*rows, Row("This month", stats.month_net)]


def _caption(row: StatDetailRow) -> str | None:
    """A sub-monthly bill's face value and cadence; an average's row count."""
    if row.transaction_count is not None:
        return _count(row.transaction_count, "row")
    if not row.frequency:
        return None
    if row.per_period_amount is not None:
        return f"{format_money(row.per_period_amount)} {row.frequency}"
    return row.frequency


def _rows(rows: list[StatDetailRow]) -> list[Row]:
    return [Row(row.label, row.value, _caption(row)) for row in rows]


def window_label(details: BudgetStatDetailsResponse) -> str:
    """``May - Jul 2026 average``, from the ``[start, end)`` window."""
    last = details.window_end - timedelta(days=1)
    return f"{details.window_start:%b} - {last:%b %Y} average"


def stat_popup(
    key: str,
    summary: BudgetSummaryResponse,
    details: BudgetStatDetailsResponse | None = None,
) -> Popup:
    """What one cell opens. The verdict and Budgets read the summary
    already on screen; income, bills and everything else need
    ``details``. An unknown key is a KeyError."""
    if key not in STAT_KEYS:
        raise KeyError(key)
    if key == "month":
        return Popup("The month, line by line", equation(summary.stats))
    if key == "budgets":
        lines = sorted(
            summary.bucket("flexible").lines, key=lambda line: -line.allocated_amount
        )
        return Popup(
            "Limits you've set",
            [
                Row(
                    line.label,
                    line.allocated_amount,
                    f"{format_money(line.spent_amount)} spent",
                )
                for line in lines
            ],
        )
    if details is None:
        raise ValueError(f"The {key} popup lists the stat details.")
    if key == "income":
        return Popup("Confirmed income", _rows(details.income))
    if key == "bills":
        return Popup(
            "Bills, monthly equivalent",
            _rows(details.bills),
            "Non-monthly bills shown at their monthly share",
        )
    return Popup(
        "Everything else",
        _rows(details.everything_else),
        f"{window_label(details)} - spending no bill or limit covers",
    )
