"""The month strip, its equation and its popups: one home, two frontends.

The web and the Flet card each built the cells, the verdict's arithmetic
and every popup's title, captions and footer, and the copies drifted: the
web's Budgets cell never said what was spent, its short month never said
how much of it was bills, and its popups lost the captions and the
window. Both now draw what ``budgets.strip`` returns.
"""

from datetime import date

import pytest

from app.services.finance.domains.planning.budgets import strip
from app.services.finance.schemas import (
    BudgetBucketResponse,
    BudgetMonthActuals,
    BudgetMonthOutlook,
    BudgetStatDetailsResponse,
    BudgetStatsResponse,
    BudgetSummaryResponse,
    StatDetailRow,
)
from tests.components.frontend._payloads import budget_line_model


def _stats(**overrides: int) -> BudgetStatsResponse:
    fields = dict(
        income_total=500_000,
        income_count=3,
        fixed_total=220_000,
        fixed_count=12,
        flexible_spent=40_000,
        flexible_allocated=150_000,
        flexible_count=5,
        on_track_count=5,
        over_budget_count=0,
        over_budget_labels=[],
        days_left_in_period=12,
        month_net=130_000,
    )
    fields.update(overrides)
    return BudgetStatsResponse(**fields)  # type: ignore[arg-type]


def _month(**overrides: int) -> BudgetMonthOutlook:
    fields = dict(
        period_month=202610,
        income_due=1_405_028,
        bills_due=1_286_346,
        budgets=370_486,
        goals=27_273,
        envelopes=4_333,
        everything_else=0,
        month_net=-283_410,
        start_balance=120_000,
        end_balance=-163_410,
    )
    fields.update(overrides)
    return BudgetMonthOutlook(**fields)


class TestTheCells:
    def test_the_four_questions_in_order(self) -> None:
        cells = strip.stats_cells(_stats())
        assert [c.key for c in cells] == ["income", "bills", "budgets", "month"]
        assert [c.label for c in cells] == ["Income", "Bills", "Budgets", "This month"]

    def test_bills_carry_their_count_and_money(self) -> None:
        bills = strip.stats_cells(_stats())[1]
        assert bills.display == "$2,200.00"
        assert bills.caption == "12 bills / month"

    def test_budgets_say_what_is_spent(self) -> None:
        budgets = strip.stats_cells(_stats())[2]
        assert budgets.display == "$1,500.00"
        assert budgets.caption == "$400.00 spent so far · 5 limits"

    def test_goals_and_envelopes_ride_the_budgets_caption(self) -> None:
        stats = _stats(goals_total=75_000, envelopes_total=4_333)
        budgets = strip.stats_cells(stats)[2]
        assert budgets.caption.endswith("+ $750.00 to goals · + $43.33 to envelopes")

    def test_a_positive_month_is_signed_and_ok(self) -> None:
        verdict = strip.stats_cells(_stats())[-1]
        assert (verdict.display, verdict.tone) == ("+$1,300.00", "ok")
        assert verdict.caption == "Left over at these settings"

    def test_a_short_month_says_so_and_counts_the_days(self) -> None:
        verdict = strip.stats_cells(_stats(month_net=-50_000))[-1]
        assert (verdict.display, verdict.tone) == ("-$500.00", "error")
        assert verdict.caption == "Short this month · 12 days left"

    def test_the_residual_names_the_part_budgets_cannot_fix(self) -> None:
        verdict = strip.stats_cells(_stats(month_net=-50_000, trim_residual=30_000))[-1]
        assert (
            verdict.caption == "Short this month · $300.00 of it is bills, not budgets"
        )

    def test_everything_else_earns_a_cell_only_when_there_is_some(self) -> None:
        cells = strip.stats_cells(_stats(everything_else=794_400))
        assert [c.key for c in cells][3] == "everything"
        assert cells[3].display == "$7,944.00"
        assert cells[3].caption == "observed · not in bills or limits"


class TestTheEquation:
    def test_it_states_the_whole_arithmetic(self) -> None:
        stats = _stats(
            income_total=1_000_000,
            fixed_total=563_896,
            flexible_allocated=289_439,
            goals_total=10_000,
            envelopes_total=4_333,
            everything_else=375_156,
            month_net=-242_824,
        )
        assert [(r.label, r.value) for r in strip.equation(stats)] == [
            ("Income", 1_000_000),
            ("Bills", -563_896),
            ("Budgets", -289_439),
            ("Goals", -10_000),
            ("Envelopes", -4_333),
            ("Everything else", -375_156),
            ("This month", -242_824),
        ]

    def test_zero_terms_stay_out(self) -> None:
        labels = [r.label for r in strip.equation(_stats())]
        assert labels == ["Income", "Bills", "Budgets", "This month"]

    def test_the_month_is_its_terms(self) -> None:
        assert (
            strip.month_net(
                income=1_000_000,
                bills=563_896,
                budgets=289_439,
                goals=10_000,
                envelopes=4_333,
                everything_else=375_156,
            )
            == -242_824
        )


class TestAMonthAhead:
    def test_the_cells_mirror_the_header(self) -> None:
        cells = strip.outlook_cells(_month())
        assert [c.label for c in cells] == [
            "Income",
            "Bills",
            "Budgets",
            "October 2026",
        ]
        assert cells[0].caption == "due that month"
        assert cells[2].caption == (
            "standing limits · + $272.73 to goals · + $43.33 to envelopes"
        )
        assert (cells[3].display, cells[3].tone) == ("-$2,834.10", "error")

    def test_a_month_ahead_gains_everything_else_too(self) -> None:
        cells = strip.outlook_cells(_month(everything_else=794_400))
        assert [c.key for c in cells] == [
            "income",
            "bills",
            "budgets",
            "everything",
            "month",
        ]

    def test_the_verdict_names_the_landing(self) -> None:
        verdict = strip.outlook_cells(_month())[-1]
        assert verdict.caption == "at these settings · ends around -$1,634.10"

    def test_the_pager_starts_now_then_names_each_landing(self) -> None:
        chips = strip.pager_chips(
            [_month(period_month=202609), _month(), _month(end_balance=124_000)]
        )
        assert [(c.label, c.tone) for c in chips] == [
            ("Now $1,200", None),
            ("Oct -$1,634", "error"),
            ("Oct $1,240", None),
        ]


class TestThePopups:
    def _summary(self) -> BudgetSummaryResponse:
        flexible = [
            budget_line_model(
                category_name="Gas", allocated_amount=10_000, spent_amount=4_000
            ),
            budget_line_model(
                category_name="Groceries", allocated_amount=60_000, spent_amount=12_345
            ),
        ]
        return BudgetSummaryResponse(
            period_month=202610,
            buckets=[
                BudgetBucketResponse(
                    name=name, total_allocated=0, total_spent=0, lines=lines
                )
                for name, lines in (
                    ("fixed", []),
                    ("non_monthly", []),
                    ("one_time", []),
                    ("flexible", flexible),
                )
            ],
            stats=_stats(),
        )

    def _details(self) -> BudgetStatDetailsResponse:
        return BudgetStatDetailsResponse(
            income=[
                StatDetailRow(label="Payroll", value=541_667, frequency="biweekly")
            ],
            bills=[
                StatDetailRow(
                    label="Car insurance",
                    value=10_000,
                    frequency="quarterly",
                    per_period_amount=30_000,
                )
            ],
            everything_else=[
                StatDetailRow(label="Dentist", value=73_209, transaction_count=3),
                StatDetailRow(label="Parking", value=1_000, transaction_count=1),
            ],
            window_start=date(2026, 5, 1),
            window_end=date(2026, 8, 1),
        )

    def test_the_verdict_opens_its_equation(self) -> None:
        popup = strip.stat_popup("month", self._summary())
        assert popup.title == "The month, line by line"
        assert popup.rows[-1].label == "This month"

    def test_budgets_list_the_limits_largest_first(self) -> None:
        popup = strip.stat_popup("budgets", self._summary())
        assert popup.title == "Limits you've set"
        assert [(r.label, r.value, r.caption) for r in popup.rows] == [
            ("Groceries", 60_000, "$123.45 spent"),
            ("Gas", 10_000, "$40.00 spent"),
        ]

    def test_income_rows_carry_their_cadence(self) -> None:
        popup = strip.stat_popup("income", self._summary(), self._details())
        assert [(r.label, r.caption) for r in popup.rows] == [("Payroll", "biweekly")]

    def test_bills_show_their_face_value_beside_the_share(self) -> None:
        popup = strip.stat_popup("bills", self._summary(), self._details())
        assert popup.rows[0].caption == "$300.00 quarterly"
        assert popup.footer == "Non-monthly bills shown at their monthly share"

    def test_everything_else_counts_rows_and_names_its_window(self) -> None:
        popup = strip.stat_popup("everything", self._summary(), self._details())
        assert [r.caption for r in popup.rows] == ["3 rows", "1 row"]
        assert popup.footer == (
            "May - Jul 2026 average - spending no bill or limit covers"
        )

    def test_the_folded_bills_read_as_one_line(self) -> None:
        summary = self._summary()
        summary.stats.fixed_total = 154_500
        summary.stats.fixed_count = 2
        assert strip.commitments_line(summary) == (
            "$1,545.00/month already committed across 2 bills"
        )
        summary.bucket("one_time").total_allocated = 23_000
        assert strip.commitments_line(summary).endswith(", plus $230.00 one-time")

    def test_an_unknown_cell_has_no_popup(self) -> None:
        with pytest.raises(KeyError):
            strip.stat_popup("vibes", self._summary())


class TestAMonthBehind:
    """#359: a month that has ended, in actuals only."""

    def test_the_cells_say_how_it_went(self) -> None:
        stats = _stats(flexible_spent=12_000, flexible_allocated=30_000)
        actuals = BudgetMonthActuals(
            period_month=202608, money_in=200_000, money_out=20_000
        )

        cells = strip.review_cells(stats, actuals)

        assert [(c.label, c.display, c.caption) for c in cells] == [
            ("Money in", "$2,000.00", "deposits, refunds and interest"),
            ("Money out", "$200.00", "everything spent, transfers aside"),
            ("Budgets", "$120.00", "of $300.00 · every limit held"),
            ("August 2026", "+$1,800.00", "left over"),
        ]
        assert cells[-1].tone == "ok"

    def test_a_month_that_ran_short_and_over_says_so(self) -> None:
        stats = _stats(over_budget_count=2)
        actuals = BudgetMonthActuals(
            period_month=202608, money_in=100_000, money_out=130_000
        )

        cells = strip.review_cells(stats, actuals)

        assert cells[2].caption.endswith("· 2 limits over")
        assert (cells[-1].display, cells[-1].caption, cells[-1].tone) == (
            "-$300.00",
            "short",
            "error",
        )

    def test_the_pager_names_the_months_behind(self) -> None:
        chips = strip.pager_chips([_month(period_month=202610)], past=[202608, 202609])
        assert [(c.label, c.tone) for c in chips] == [
            ("Aug", None),
            ("Sep", None),
            ("Now $1,200", None),
        ]
