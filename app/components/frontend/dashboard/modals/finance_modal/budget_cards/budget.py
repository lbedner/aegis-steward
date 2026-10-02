"""What a budget line or a month's outlook shows.

The stat cells both tabs put above their table, the compact row
the lines tab repeats, and the chips and captions that annotate
them.
"""

from typing import Any

import flet as ft

from app.components.frontend.controls import (
    NumericText,
    SecondaryText,
)
from app.components.frontend.controls.table import TableNameText

# Named rows in the import review's detail sections before the tail folds
# into a count. A Quicken tree can carry hundreds of new categories, and a
# dialog that scrolls for a page stops being read at all.
# One height for every Overview card, so the row has a single baseline.
# Named slices in the spending donut (and rows in the list under it) before
# the tail folds into "Other". Five left "Other" as the biggest slice on any
# real ledger, which hides exactly the breakdown the card exists to show.
# Measured against a real ledger (23 parent-level categories after the
# spending_by_category rollup): 10 slices still left "Other" at 16.3%; 15
# gets it to 5.3%, with everything past #15 individually under 1% of total
# spend - the tail at that point really is "everything else", not a few
# disguised top categories. PieChartCard's legend scrolls within its fixed
# height (modal_sections.py) rather than clipping, so this isn't bounded
# by legend space anymore.
from app.components.frontend.dashboard.modals.finance_modal.formatting import (
    _budget_status_color,
    _usd,
)
from app.components.frontend.theme import AegisTheme as Theme


def budget_lines_grid(rows: list[ft.Control]) -> ft.ResponsiveRow:
    """Budget lines as a flowing grid, three per row when there is room.

    Full-width stacking gave a dozen lines a page of scrolling for no
    information - each line is a label, a small bar and two numbers.
    12-grid columns: 4 on a large window (three per row), 6 on a middling
    one (two), 12 when cramped - the narrow case degrades to exactly the
    old one-per-row layout rather than crushing the bars.
    """
    return ft.ResponsiveRow(
        [ft.Container(content=row, col={"sm": 12, "md": 6, "lg": 4}) for row in rows],
        spacing=Theme.Spacing.MD,
        run_spacing=Theme.Spacing.SM,
    )


def compact_budget_row(line: dict[str, Any]) -> ft.Control:
    """One flexible budget line, on the trim rows' geometry.

    The previous row stacked label / 8px bar / a 16px-bold percent line -
    three storeys per limit, so a dozen limits filled the screen while
    "Close the gap" fit twelve rows in four lines. Same shape as a trim
    row now: name over the figures on the left, one right-aligned percent,
    and the bar slimmed to a 4px strip between them.

    The bar clamps at 100% (Flet's ``ProgressBar`` has no over-100
    concept) but the PERCENT never lies: an overrun reads "129%", in
    error red. Monochrome-first everywhere else - a healthy line's
    percent carries no accent at all.
    """
    status = line.get("status", "good")
    spent, allocated = line.get("spent_amount", 0), line.get("allocated_amount", 0)
    color = _budget_status_color(status)
    pct_color = (
        Theme.Colors.ERROR
        if status == "critical"
        else Theme.Colors.WARNING
        if status == "warn"
        else Theme.Colors.TEXT_SECONDARY
    )
    return ft.Column(
        [
            ft.Row(
                [
                    ft.Container(content=TableNameText(line["label"]), expand=True),
                    NumericText(
                        f"{line.get('spent_percent', 0)}%",
                        size=Theme.Typography.BODY_SMALL,
                        color=pct_color,
                    ),
                ],
                spacing=Theme.Spacing.SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.ProgressBar(
                value=min(line.get("spent_ratio", 0.0), 1.0),
                height=4,
                color=color,
                bgcolor=ft.Colors.with_opacity(0.15, ft.Colors.ON_SURFACE),
                border_radius=2,
            ),
            SecondaryText(
                f"{_usd(spent)} of {_usd(allocated)}",
                size=Theme.Typography.BODY_SMALL,
            ),
        ],
        spacing=Theme.Spacing.XS,
        tight=True,
    )
