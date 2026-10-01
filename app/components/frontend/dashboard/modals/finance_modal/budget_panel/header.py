"""What sits above the budget tabs: the stats strip and the pager."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

import flet as ft

from app.components.frontend.controls import (
    NumericText,
    SecondaryText,
)
from app.components.frontend.dashboard.modals.finance_modal.budget_panel.base import (
    BudgetPanelState,
)
from app.components.frontend.theme import AegisTheme as Theme
from app.services.finance.domains.planning.budgets import strip
from app.services.finance.schemas import (
    BudgetMonthOutlook,
    BudgetStatDetailsResponse,
    BudgetSummaryResponse,
)

# The colour a strip cell's or a pager chip's tone wears here.
_TONES = {"ok": Theme.Colors.ACCENT, "error": Theme.Colors.ERROR}


class BudgetHeaderMixin(BudgetPanelState):
    """The month you are looking at, and the four numbers for it.

    Each stat opens its own rows; the pager moves the whole panel a
    month at a time without refetching.
    """

    def _stats_strip(self) -> ft.Control:
        # Paged past "this month", the cells recompute for that future
        # month (bills at face value on their real cadence); index 0
        # keeps the classic monthly-equivalent header.
        if 0 < self._outlook_index < len(self._outlook):
            month = BudgetMonthOutlook.model_validate(
                self._outlook[self._outlook_index]
            )
            # Future months carry no per-row backup yet, so the cells
            # stay plain there.
            cells = [
                self._stat_cell(c.label, c.display, c.caption, _TONES.get(c.tone))
                for c in strip.outlook_cells(month)
            ]
        else:
            summary = BudgetSummaryResponse.model_validate(self._summary)
            cells = [
                self._stat_cell(
                    c.label,
                    c.display,
                    c.caption,
                    _TONES.get(c.tone),
                    on_tap=lambda e, k=c.key: self._open_stat_detail(k, e),
                )
                for c in strip.stats_cells(summary.stats)
            ]
        return ft.Container(
            content=ft.Row(cells, spacing=Theme.Spacing.LG),
            border=ft.border.all(1, ft.Colors.OUTLINE),
            border_radius=Theme.Components.CARD_RADIUS,
            bgcolor=ft.Colors.SURFACE,
            padding=ft.padding.symmetric(
                horizontal=Theme.Spacing.LG, vertical=Theme.Spacing.SM
            ),
        )

    def _open_stat_detail(self, key: str, e: ft.ControlEvent) -> None:
        if self.page is not None:
            self.page.run_task(self._open_stat_detail_async, key, e)

    async def _open_stat_detail_async(self, key: str, e: ft.ControlEvent) -> None:
        """Rows for whichever cell was clicked, as ``strip`` words them. The
        verdict and Budgets build from the summary already on screen (zero
        fetch, cannot disagree with the strip); the rest come from one
        cached /budget/stat-details fetch."""
        details = None
        if key in strip.DETAIL_KEYS:
            if self._stat_details is None:
                from app.components.frontend.state.session_state import (
                    get_session_state,
                )

                api = get_session_state(self.page).api_client
                data = await api.get(
                    "/api/v1/finance/budget/stat-details",
                    params=self._account_filter.params(),
                )
                if not isinstance(data, dict):
                    return
                self._stat_details = data
            details = BudgetStatDetailsResponse.model_validate(self._stat_details)
        popup = strip.stat_popup(
            key, BudgetSummaryResponse.model_validate(self._summary), details
        )
        self._stat_detail.open_at(
            e,
            popup.title,
            [asdict(row) for row in popup.rows],
            footer=popup.footer or None,
        )

    def _month_pager(self) -> ft.Control:
        """The months ahead as one row: arrows page the header, the chips
        name each month's verdict - the October that breaks even is
        visible without going looking for it."""
        if not self._outlook:
            return ft.Container()

        def _page(delta: int) -> None:
            self._outlook_index = max(
                0, min(len(self._outlook) - 1, self._outlook_index + delta)
            )
            self._render()

        def _jump(index: int) -> None:
            self._outlook_index = index
            self._render()

        chips: list[ft.Control] = []
        months = [BudgetMonthOutlook.model_validate(e) for e in self._outlook]
        for i, chip in enumerate(strip.pager_chips(months)):
            label = chip.label
            color = _TONES.get(chip.tone, Theme.Colors.TEXT_SECONDARY)
            selected = i == self._outlook_index
            chips.append(
                ft.Container(
                    content=SecondaryText(
                        label,
                        size=Theme.Typography.BODY_SMALL,
                        color=color,
                        weight=ft.FontWeight.W_600 if selected else None,
                    ),
                    padding=ft.padding.symmetric(horizontal=8, vertical=3),
                    border_radius=Theme.Components.BUTTON_RADIUS,
                    border=ft.border.all(
                        1,
                        Theme.Colors.ACCENT if selected else Theme.Colors.BORDER_SUBTLE,
                    ),
                    on_click=lambda _e, i=i: _jump(i),
                    ink=True,
                )
            )
        return ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.CHEVRON_LEFT,
                    icon_size=16,
                    icon_color=ft.Colors.ON_SURFACE_VARIANT,
                    tooltip="Previous month",
                    on_click=lambda _e: _page(-1),
                ),
                *chips,
                ft.IconButton(
                    icon=ft.Icons.CHEVRON_RIGHT,
                    icon_size=16,
                    icon_color=ft.Colors.ON_SURFACE_VARIANT,
                    tooltip="Next month",
                    on_click=lambda _e: _page(1),
                ),
            ],
            spacing=Theme.Spacing.XS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            wrap=True,
        )

    def _stat_cell(
        self,
        label: str,
        value: str,
        caption: str,
        color: str | None = None,
        on_tap: Callable[[ft.ControlEvent], None] | None = None,
    ) -> ft.Control:
        cell = ft.Column(
            [
                SecondaryText(label.upper(), size=Theme.Typography.CAPTION),
                NumericText(
                    value,
                    size=22,
                    weight=Theme.Typography.WEIGHT_BOLD,
                    color=color or Theme.Colors.TEXT_PRIMARY,
                ),
                SecondaryText(caption, size=Theme.Typography.BODY_SMALL),
            ],
            spacing=2,
        )
        if on_tap is None:
            cell.expand = True
            return cell
        # on_tap_down, not on_click: the popup anchors at the tap's own
        # coordinates (the same mechanics every picker trigger uses).
        return ft.Container(
            content=cell,
            expand=True,
            ink=True,
            border_radius=Theme.Components.BUTTON_RADIUS,
            on_tap_down=on_tap,
            on_click=lambda _e: None,
            tooltip="Click for the breakdown",
        )
