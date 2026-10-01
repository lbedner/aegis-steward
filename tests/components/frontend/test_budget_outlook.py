"""The Budget header: bills, budget, income, and the month's verdict.

The old strip led with flexible-spending percentages and an "On track"
count - process numbers. The question the tab exists to answer is "do
these settings clear the month", and that needs exactly four figures:
what comes in, what the bills take, what the budgets take, and the
signed remainder.
"""

from app.components.frontend.dashboard.modals import finance_modal
from tests.components.frontend._tree import panel_source


class TestTheWiring:
    def test_the_panel_draws_the_shared_strip(self) -> None:
        """The cells, the months ahead and the pager are ``budgets.strip``'s,
        the same ones the web draws (tests/services/test_budget_strip.py)."""
        import inspect

        source = inspect.getsource(finance_modal.BudgetPanel._stats_strip)
        assert "strip.stats_cells(" in source
        assert "strip.outlook_cells(" in source
        pager = inspect.getsource(finance_modal.BudgetPanel._month_pager)
        assert "strip.pager_chips(" in pager

    def test_a_line_amount_opens_the_edit_dialog(self) -> None:
        """Budgets are adjustable in place - that is the whole "tune it
        and watch the month react" loop."""
        import inspect

        source = inspect.getsource(finance_modal.BudgetPanel._line_row)
        assert "_open_edit_limit" in source

    def test_a_negative_month_offers_its_trims(self) -> None:
        source = panel_source(finance_modal.BudgetPanel)
        assert "_trims_section" in source
        assert "trims" in source


class TestHeaderStaysShort:
    """The Budget header spends its height on numbers, not prose: the
    explainer sentence rides an info-icon tooltip (not its own line) and
    the month pager shares the title's row instead of claiming one."""

    def test_the_explainer_is_a_tooltip_not_a_line(self) -> None:
        import inspect

        source = inspect.getsource(finance_modal.BudgetPanel.__init__)
        start = source.index("Your plan checked against")
        assert "tooltip=(" in source[max(0, start - 200) : start]

    def test_the_pager_rides_the_title_row(self) -> None:
        import inspect

        assert "_pager_slot" in inspect.getsource(finance_modal.BudgetPanel.__init__)
        strip = inspect.getsource(finance_modal.BudgetPanel._stats_strip)
        assert "_month_pager" not in strip


class TestStatPopups:
    """Click a header cell, get its rows: one shared popup, five feeds,
    each worded by ``budgets.strip`` for the web and here alike."""

    def test_the_panel_renders_rows_and_footer(self) -> None:
        from app.components.frontend.dashboard.modals.finance_modal import (
            stat_detail_panel,
        )

        panel = stat_detail_panel(
            "Everything else",
            [
                {"label": "Dentist", "value": 73_209, "caption": "3 rows"},
                {"label": "Cash & ATM", "value": 22_000, "caption": "6 rows"},
            ],
            footer="May - Jul 2026 average",
        )

        def texts(node):
            found = []
            value = getattr(node, "value", None)
            if isinstance(value, str) and value:
                found.append(value)
            for child in getattr(node, "controls", None) or []:
                found.extend(texts(child))
            content = getattr(node, "content", None)
            if content is not None:
                found.extend(texts(content))
            return found

        rendered = " ".join(texts(panel))
        assert "Dentist" in rendered
        assert "$732.09" in rendered
        assert "3 rows" in rendered
        assert "May - Jul 2026 average" in rendered

    def test_the_cells_are_wired_to_open_it(self) -> None:
        source = panel_source(finance_modal.BudgetPanel)
        assert "_stat_detail" in source
        assert "_open_stat_detail" in source
        assert "stat-details" in source
        assert "strip.stat_popup(" in source
