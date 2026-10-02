"""The One-time group of the Budget card: deliberate plans, face value on
a date.

A one-off stream is an entry the user typed in on purpose - a dentist
visit, a gift - not detector noise. It has no monthly share, so it lives
outside the Fixed/Non-monthly "/mo" sections, but hiding it entirely hid
plans the user made deliberately. Module-level functions, not mixin
methods: the group reads straight from its bucket and touches no panel
state.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import flet as ft

from app.components.frontend.controls import (
    H3Text,
    NumericText,
    SecondaryText,
    SectionCard,
)
from app.components.frontend.controls.table import TableNameText
from app.components.frontend.dashboard.modals.finance_modal.budget_cards import (
    budget_lines_grid,
)
from app.components.frontend.dashboard.modals.finance_modal.formatting import _usd
from app.components.frontend.theme import AegisTheme as Theme


def one_time_section(
    bucket: dict[str, Any], title: str, note: str
) -> ft.Control | None:
    """Face value and a date, never a "/mo": amortizing a one-off is
    exactly the mistake the monthly buckets exist to avoid. Absent
    entirely when empty - an empty prompt would nag about a kind of
    entry most months don't have."""
    lines = bucket["lines"]
    if not lines:
        return None
    return SectionCard(
        title=H3Text(title),
        body=budget_lines_grid([_one_time_row(line) for line in lines]),
        actions=[SecondaryText(f"{_usd(bucket['total_allocated'])} {note}")],
        body_padding=Theme.Spacing.MD,
    )


def _one_time_row(line: dict[str, Any]) -> ft.Control:
    due = line.get("due_date")
    when = date.fromisoformat(due) if due else None
    return ft.Row(
        [
            TableNameText(line["label"]),
            ft.Container(expand=True),
            NumericText(_usd(line.get("allocated_amount", 0)), size=14),
            SecondaryText(f"{when.strftime('%b')} {when.day}" if when else "No date"),
        ],
        spacing=Theme.Spacing.MD,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
