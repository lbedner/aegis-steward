"""Budget: the month header, its limits, suggestions, goals and envelopes.

One page, four tabs on one URL. The stats strip answers "does this month
clear" and pages through the months ahead (pattern 3); every cell opens
its arithmetic in the dialog (pattern 4). Lines, suggestions, goals and
envelopes are row actions (pattern 2) and dialog forms (pattern 1); each
change ships the strip back out of band so the verdict never goes stale.
"""

import calendar
from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from app.services.finance.utils import (
    current_date,
    current_period_month,
    period_label,
    shift_period,
)
from tests.components.frontend._payloads import budget_line_model
from tests.services._finance_factories import seed_limit
from tests.web.conftest import Budget, Ledger
from tests.web.dom import (
    id_of,
    location,
    none,
    one,
    oob,
    select,
    stats,
    text,
    triggers,
)


def _line(allocated: int, spent: int) -> Any:
    """A flexible line as the page receives it."""
    return budget_line_model(
        category_id=1,
        category_name="Gas & Fuel",
        allocated_amount=allocated,
        spent_amount=spent,
    )


class TestPage:
    def test_tabs_and_the_strip(self, client: TestClient, budget: Budget) -> None:
        page = client.get("/budget").text
        tabs = [text(a) for a in select(page, "#budget [role=tablist] a")]
        assert tabs == ["Limits", "Suggested", "Goals (1)", "Envelopes (1)"]
        strip = stats(page)
        assert list(strip)[:3] == ["Income", "Bills", "Budgets"]
        assert strip["Budgets"] == "$200.00"
        assert "This month" in strip

    def test_every_cell_opens_its_details(
        self, client: TestClient, budget: Budget
    ) -> None:
        page = client.get("/budget").text
        for cell in select(page, "#budget-stats [hx-get]"):
            assert cell.get("hx-get", "").startswith("/budget/stats/")
            assert cell.get("hx-target") == "#dialog-body"

    def test_month_pager_walks_the_outlook(
        self, client: TestClient, budget: Budget
    ) -> None:
        page = client.get("/budget").text
        chips = select(page, "#month-pager a:not([aria-label])")  # the arrows aside
        # Six months behind (#359), now, and five ahead.
        assert len(chips) == 12 and text(chips[6]).startswith("Now")
        assert chips[7].get("hx-target") == "#budget"
        ahead = client.get("/budget?month=1").text
        # A future month's verdict is titled with the month, and the cells
        # are figures, not doors: nothing to drill into yet.
        assert "This month" not in stats(ahead)
        none(ahead, "#budget-stats [hx-get]")
        assert (
            one(ahead, "#month-pager a[aria-current]").get("href")
            == "/budget?month=1&tab=limits"
        )
        assert (
            one(ahead, '#month-pager a[aria-label="Previous month"]').get("href")
            == "/budget?month=0&tab=limits"
        )

    def test_every_pager_and_tab_link_goes_where_it_swaps(
        self, client: TestClient, budget: Budget
    ) -> None:
        """Each link built its URL twice, and the href dropped the tab or the
        month that the hx-get kept: opened in a new tab, it lost its place."""
        page = client.get("/budget?month=1&tab=goals").text
        links = select(page, "#month-pager a") + select(
            page, "#budget [role=tablist] a"
        )

        assert links
        for link in links:
            href = link.get("href") or ""
            assert href == link.get("hx-get"), href
            assert "month=" in href and "tab=" in href, href

    def test_filters_replace_the_page_in_place(
        self, client: TestClient, budget: Budget
    ) -> None:
        page = client.get("/budget").text
        form = one(page, "form#filter")
        assert form.get("hx-target") == "#app-content"
        none(form, 'input[name="days"]')  # no time window on a month view

    def test_fragment_has_no_shell(self, hx: TestClient, budget: Budget) -> None:
        fragment = hx.get("/budget").text
        none(fragment, "html")
        one(fragment, "#budget")


class TestStatDetails:
    def test_the_month_is_its_own_arithmetic(
        self, hx: TestClient, budget: Budget
    ) -> None:
        dialog = hx.get("/budget/stats/month").text
        rows = [text(li) for li in select(dialog, "#stat-rows li")]
        assert rows[0].startswith("Income") and rows[-1].startswith("This month")

    def test_budgets_lists_the_limits(self, hx: TestClient, budget: Budget) -> None:
        dialog = hx.get("/budget/stats/budgets").text
        row = one(dialog, "#stat-rows li")
        assert "Food:Groceries" in text(row) and "$200.00" in text(row)

    def test_bills_come_from_the_details_endpoint(
        self, hx: TestClient, budget: Budget
    ) -> None:
        dialog = hx.get("/budget/stats/bills").text
        assert "Rent" in text(one(dialog, "#stat-rows"))

    def test_everything_else_names_its_window(
        self, hx: TestClient, budget: Budget
    ) -> None:
        """The Flet popup said which months the average covers; the web's
        only said what the spending was not."""
        dialog = hx.get("/budget/stats/everything").text
        assert text(one(dialog, "#stat-footer")).endswith(
            "average - spending no bill or limit covers"
        )

    def test_the_budgets_cell_says_what_is_spent(
        self, client: TestClient, budget: Budget
    ) -> None:
        """The Flet card said what the limits had spent; the web's cell
        only counted them. Both draw ``budgets.strip``'s cell now."""
        page = client.get("/budget").text
        cell = next(
            div
            for div in select(page, "#budget-stats > div")
            if text(one(div, "dt")) == "Budgets"
        )
        assert text(select(cell, "dd")[-1]).endswith("spent so far · 1 limit")

    def test_a_limit_opens_what_it_is_made_of(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        """ "$535.16 of $1,000.00" was a figure with no way to ask what
        it was made of."""
        page = client.get("/budget").text
        opener = one(page, f'[hx-get="/budget/lines/{budget.line}/transactions"]')
        assert opener.get("hx-target") == "#dialog-body"

        body = hx.get(f"/budget/lines/{budget.line}/transactions").text

        assert "Food:Groceries" in text(one(body, "h2"))
        assert select(body, "tbody tr"), "no transactions behind the limit"

    def test_the_opener_states_its_own_swap(
        self, client: TestClient, budget: Budget
    ) -> None:
        """htmx INHERITS hx-swap from ancestors, and this opener sits
        inside the limit's row form, which swaps ITSELF outerHTML. The
        modal borrowed that and replaced #dialog-body with the dialog's
        own content: it opened once, and every later open failed with
        htmx:targetError because the element it targets was gone - "I
        can't open a new one until I refresh". ``hx_dialog`` states the
        swap so nothing can be borrowed against it."""
        page = client.get("/budget").text
        opener = one(page, f'[hx-get="/budget/lines/{budget.line}/transactions"]')

        assert opener.get("hx-swap") == "innerHTML"

    def test_a_renamed_row_shows_the_name_it_was_given(
        self, client: TestClient, hx: TestClient, budget: Budget, ledger: Ledger
    ) -> None:
        """Live: the drill-down listed "SHPRTE NTH RD&WNSW GT
        XXX-XXX-6086 NY 09/11" for a row long since named Shop Rite.
        It read ``name``, the raw descriptor, where every other
        transaction list reads ``payee_label`` - whose own docstring
        says showing the descriptor after a rename reads as though the
        rename never took."""
        body = hx.get(f"/budget/lines/{budget.line}/transactions").text

        headers = [text(th) for th in select(body, "thead th")]

        assert "Payee" in headers and "Name" not in headers

    def test_the_rows_add_up_to_the_figure_they_were_opened_from(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        """The point of a drill-down. The spend comes from one tally
        over the period's outflows, so the list answers with the same
        window, the same predicate and the same matching - exact
        category, never the parent-prefix rollup the Overview pie uses,
        because a limit on a leaf never included its siblings."""
        body = hx.get(f"/budget/lines/{budget.line}/transactions").text

        subtitle = text(one(body, "p"))
        shown = sum(
            int(
                round(
                    float(text(tr.getchildren()[3]).replace("$", "").replace(",", ""))
                    * 100
                )
            )
            for tr in select(body, "tbody tr")
        )
        assert f"${abs(shown) / 100:,.2f}" in subtitle

    def test_unknown_cell_is_404(self, hx: TestClient, budget: Budget) -> None:
        assert hx.get("/budget/stats/nope").status_code == 404


class TestLines:
    def test_flexible_line_is_an_inline_form(
        self, client: TestClient, budget: Budget
    ) -> None:
        page = client.get("/budget").text
        line = one(page, f"#line-{budget.line}")
        form = one(line, "form")
        assert form.get("hx-post") == "/budget/lines"
        assert form.get("hx-target") == f"#line-{budget.line}"
        assert one(form, 'input[name="category_id"]').get("value") == str(
            budget.groceries
        )
        assert one(form, 'input[name="allocated_amount"]').get("value") == "200.00"
        # Spent so far this month: the ledger's groceries are $30.00 today
        # and $15.00 yesterday, and on the 1st yesterday is last month's
        # (CI, 2026-10-01).
        spent = 3_000 + (1_500 if current_date().day > 1 else 0)
        assert f"${spent / 100:,.2f}" in text(line)
        one(line, f'[hx-get="/budget/lines/{budget.line}/remove"]')

    def test_the_limit_opens_for_editing_on_a_click(
        self, client: TestClient, budget: Budget
    ) -> None:
        """The limit reads as a figure with a pencil; a click swaps in the
        input, so a stray click on the row never edits it."""
        line = one(client.get("/budget").text, f"#line-{budget.line}")
        edit = one(line, "button[data-edit]")
        assert "$200.00" in text(edit)
        assert "Edit limit" in text(one(edit, ".sr-only"))
        one(edit, 'use[href="#i-pencil"]')
        box = one(line, '[x-show="editing"][x-cloak]')
        assert one(box, 'input[name="allocated_amount"]').get("value") == "200.00"

    @pytest.mark.queryspy(threshold=3)  # the row and the strip each carry
    def test_a_limit_rolls_over_from_its_row(
        self, client: TestClient, budget: Budget
    ) -> None:
        """#360: the row's own form says rollover, so ticking it is one
        post, and unticking it is too."""
        row = one(client.get("/budget").text, f"#line-{budget.line}")
        box = one(row, 'form input[type="checkbox"][name="rollover"]')
        assert box.get("checked") is None

        def post(**extra: str) -> Any:
            response = client.post(
                "/budget/lines",
                data={
                    "category_id": str(budget.groceries),
                    "allocated_amount": "200",
                    "rollover_sent": "1",
                    **extra,
                },
            )
            primary, _siblings = oob(response.text)
            return one(primary[0], 'input[name="rollover"]')

        assert post(rollover="on").get("checked") is not None
        assert post().get("checked") is None

    @pytest.mark.queryspy(threshold=5)  # two seeded, the page, the drill-down
    async def test_a_rolling_limit_says_what_it_carried(
        self,
        client: TestClient,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        budget: Budget,
    ) -> None:
        """Last month's books limit went unspent, so this month's $100 has
        $200 to spend: the row and its drill-down say so."""
        books = await finance.get_or_create_category_from_hint("Shopping:Books")
        last_month = shift_period(current_period_month(), -1)
        await seed_limit(
            finance,
            books.id,
            10_000,
            owner_user_id=None,
            period_month=last_month,
            rollover=True,
        )
        line = await seed_limit(
            finance, books.id, 10_000, owner_user_id=None, rollover=True
        )
        await async_db_session.commit()

        row = one(client.get("/budget").text, f"#line-{line.id}")

        assert text(one(row, "[data-available]")) == "$200.00"  # what is left leads
        assert text(one(row, "[data-carried]")) == "+$100.00 carried"
        one(row, 'input[name="allocated_amount"][value="100.00"]')  # still the limit
        body = hx.get(f"/budget/lines/{line.id}/transactions").text
        title = select(body, "h2")[0]  # the empty state carries a heading too
        assert text(title.getnext()) == "$0.00 of $200.00 this month"

    def test_editing_the_amount_swaps_the_row_and_the_strip(
        self, client: TestClient, budget: Budget
    ) -> None:
        response = client.post(
            "/budget/lines",
            data={"category_id": str(budget.groceries), "allocated_amount": "250"},
        )
        assert response.status_code == 200
        primary, siblings = oob(response.text)
        assert primary[0].get("id") == f"line-{budget.line}"
        assert (
            one(primary[0], 'input[name="allocated_amount"]').get("value") == "250.00"
        )
        strip = next(s for s in siblings if s.get("id") == "budget-stats")
        assert "$250.00" in text(strip)

    def test_the_strip_a_write_ships_still_opens_its_cells(
        self, client: TestClient, budget: Budget
    ) -> None:
        """The strip a line write sends back was rendered without the section
        path, so after an edit or a removal every cell asked for
        /stats/<key>, which is no route."""
        edited = client.post(
            "/budget/lines",
            data={"category_id": str(budget.groceries), "allocated_amount": "250"},
        )
        removed = client.delete(f"/budget/lines/{budget.line}")
        for response in (edited, removed):
            _primary, siblings = oob(response.text)
            strip = next(s for s in siblings if s.get("id") == "budget-stats")
            openers = [cell.get("hx-get") for cell in select(strip, "[hx-get]")]
            assert openers, "the strip ships no openers"
            assert all(url.startswith("/budget/stats/") for url in openers), openers

    def test_removing_a_limit_asks_then_takes_the_row_away(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        """Every removal asks in the app's own dialog (limits used the
        browser's confirm()); the answer takes the row off the page and
        moves the strip."""
        row = one(client.get("/budget").text, f"#line-{budget.line}")
        opener = one(row, f'[hx-get="/budget/lines/{budget.line}/remove"]')
        assert opener.get("hx-target") == "#dialog-body"
        none(row, "[hx-confirm]")
        confirm = hx.get(f"/budget/lines/{budget.line}/remove").text
        one(confirm, f'[hx-delete="/budget/lines/{budget.line}"]')

        response = client.delete(f"/budget/lines/{budget.line}")

        primary, siblings = oob(response.text)
        assert primary == []
        assert {s.get("id"): s.get("hx-swap-oob") for s in siblings} == {
            f"line-{budget.line}": "delete",
            "budget-stats": "outerHTML",
        }
        assert "dialog:close" in triggers(response)
        assert stats(client.get("/budget").text)["Budgets"] == "$0.00"

    def test_add_a_limit_from_the_dialog(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        button = one(client.get("/budget").text, '[hx-get="/budget/lines/new"]')
        assert button.get("hx-target") == "#dialog-body"
        form = one(hx.get("/budget/lines/new").text, "form")
        assert form.get("hx-post") == "/budget/lines"
        one(form, 'select[name="category_id"]')
        response = client.post(
            "/budget/lines",
            data={
                "category_id": str(budget.fuel),
                "allocated_amount": "80",
                "source": "dialog",
            },
        )
        assert location(response) == "/budget"
        assert "dialog:close" in triggers(response)
        assert "Auto:Fuel" in text(one(client.get("/budget").text, "#limits"))

    def test_bad_amount_is_a_422(self, client: TestClient, budget: Budget) -> None:
        response = client.post(
            "/budget/lines",
            data={"category_id": str(budget.fuel), "allocated_amount": "lots"},
        )
        assert response.status_code == 422

    def test_commitments_list_the_bills(
        self, client: TestClient, budget: Budget
    ) -> None:
        page = client.get("/budget").text
        # The summary rolls bills up by category, so the rent shows as its
        # monthly figure, not by name.
        assert "$1,500.00" in text(one(page, "#commitments"))

    async def test_a_one_off_reads_by_the_name_it_was_given(
        self,
        client: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        budget: Budget,
    ) -> None:
        """A plan typed in as "Dentist" reads "Dentist", as the card shows
        it, not as the category it was filed under."""
        dentist = await finance.create_recurring_stream(
            owner_user_id=None,
            name="Dentist",
            direction="outflow",
            frequency="once",
            expected_amount=23_000,
            next_expected_date=current_date(),
        )
        dentist.category_id = budget.fuel
        async_db_session.add(dentist)
        await async_db_session.commit()

        page = client.get("/budget").text

        names = [text(s) for s in select(page, "#commitments li > span:first-child")]
        assert "Dentist" in names
        assert "Auto:Fuel" not in names


class TestSuggestions:
    def test_empty_when_history_is_thin(
        self, client: TestClient, budget: Budget
    ) -> None:
        page = client.get("/budget?tab=suggested").text
        assert "Nothing to suggest" in text(one(page, "#suggestions"))

    def test_the_card_names_the_window_its_picks_came_from(
        self, client: TestClient, budget: Budget
    ) -> None:
        """The window is the gate's, sent with the picks; the card and the
        Flet panel each used to say "six" on their own."""
        page = client.get("/budget?tab=suggested").text
        assert "what 6 months of spending already imply" in text(
            one(page, "#suggestions")
        )

    def test_dismiss_then_restore_re_render_the_section(
        self, client: TestClient, budget: Budget
    ) -> None:
        dismissed = client.post(f"/budget/suggestions/{budget.fuel}/dismiss")
        section = one(dismissed.text, "#suggestions")
        assert "1 dismissed" in text(section)
        restore = one(section, f'[hx-post="/budget/suggestions/{budget.fuel}/restore"]')
        assert restore.get("hx-target") == "#suggestions"
        restored = client.post(f"/budget/suggestions/{budget.fuel}/restore")
        assert "dismissed" not in text(one(restored.text, "#suggestions"))

    def test_accepting_creates_the_line_and_moves_the_strip(
        self, client: TestClient, budget: Budget
    ) -> None:
        response = client.post(
            f"/budget/suggestions/{budget.fuel}/accept", data={"amount": "15"}
        )
        primary, siblings = oob(response.text)
        assert primary[0].get("id") == "suggestions"
        assert siblings[0].get("id") == "budget-stats"
        assert "$215.00" in text(siblings[0])  # 200.00 + 15.00
        assert "Auto:Fuel" in text(one(client.get("/budget").text, "#limits"))

    def test_goal_parse_round_trip(self, client: TestClient, budget: Budget) -> None:
        form = one(client.get("/budget?tab=suggested").text, "form#goal-parse")
        assert form.get("hx-post") == "/budget/goal"
        unmatched = client.post("/budget/goal", data={"text": "zzz nothing"})
        assert unmatched.status_code == 422
        one(unmatched.text, '[role="alert"]')
        matched = client.post("/budget/goal", data={"text": "cut back on Market"})
        if matched.status_code == 200:
            confirm = one(matched.text, 'form[hx-post="/budget/lines"]')
            assert one(confirm, 'input[name="allocated_amount"]').get("value")
            assert "Suggested limit" in matched.text


class TestGoals:
    def test_card_shows_progress_and_verbs(
        self, client: TestClient, budget: Budget
    ) -> None:
        page = client.get("/budget?tab=goals").text
        card = one(page, f"#goal-{budget.goal}")
        assert "Vacation" in text(card) and "25%" in text(card)
        assert one(card, "progress").get("value") == "0.25"
        pause = one(card, f'[hx-post="/budget/goals/{budget.goal}/pause"]')
        assert pause.get("hx-target") == f"#goal-{budget.goal}"
        one(card, f'[hx-get="/budget/goals/{budget.goal}/contribute"]')
        one(card, f'[hx-get="/budget/goals/{budget.goal}/edit"]')

    def test_the_percent_shown_is_the_percent_judged(self) -> None:
        """The tone flips at exactly 80% of the limit. Rounding 79.96%
        up to "80%" showed a figure that had reached the threshold
        beside a bar that had not - live, on Gas & Fuel."""
        from app.services.finance.domains.planning.budgets.lines import (
            budget_line_status,
        )

        allocated, spent = 20_000, 15_992  # 79.96%

        assert _line(allocated, spent).spent_percent == 79
        assert budget_line_status(allocated, spent) == "good"
        assert budget_line_status(allocated, 16_000) == "warn"

    @pytest.mark.parametrize(
        ("spent", "red"), [(19_999, False), (20_000, True), (25_000, True)]
    )
    def test_the_percent_turns_red_where_the_bar_does(
        self, spent: int, red: bool
    ) -> None:
        """At exactly 100% the bar turned red and the percent did not: the
        template judged the percent itself, at more than 100%."""
        from app.components.web_frontend.rendering import templates

        row = templates.get_template("partials/budget/line.html").render(
            line=_line(20_000, spent), path="/budget"
        )

        assert (
            "text-error" in (one(row, "li span.tabular-nums").get("class") or "")
        ) is red

    def test_a_limit_bar_carries_its_tone_as_a_text_colour(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Reported live 2026-09-12: every limit bar rendered the same
        green, the 84%-spent one included. ``accent-color`` is the
        documented way to tint a ``<progress>`` and Chrome paints its
        own colour anyway, so the tone never reached the screen. The
        fill is painted from ``currentColor`` now, which means the tone
        has to arrive as a text-* class."""
        from app.components.web_frontend.rendering import templates

        macros = templates.get_template("components/macros/layout.html").module

        assert "text-aegis-teal" in macros.progress(0.5, "ok")
        assert "text-aegis-amber" in macros.progress(0.85, "warn")
        assert "text-error" in macros.progress(1.2, "error")
        assert "accent-" not in macros.progress(0.5, "warn")

    def test_pause_toggles_in_place(self, client: TestClient, budget: Budget) -> None:
        paused = client.post(f"/budget/goals/{budget.goal}/pause").text
        card = one(paused, f"#goal-{budget.goal}")
        assert (
            "Paused" in text(card)
            and text(one(card, "[hx-post$='/pause']")) == "Resume"
        )
        resumed = client.post(f"/budget/goals/{budget.goal}/pause").text
        assert text(one(resumed, "[hx-post$='/pause']")) == "Pause"

    def test_contribute_dialog_updates_the_card(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        form = one(hx.get(f"/budget/goals/{budget.goal}/contribute").text, "form")
        assert form.get("hx-post") == f"/budget/goals/{budget.goal}/contribute"
        response = client.post(
            f"/budget/goals/{budget.goal}/contribute", data={"amount": "100"}
        )
        card = one(response.text, f"#goal-{budget.goal}[hx-swap-oob]")
        assert "$350.00" in text(card) and "35%" in text(card)
        assert "dialog:close" in triggers(response)

    def test_editor_previews_a_relative_target(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        form = one(hx.get("/budget/goals/new").text, "form")
        assert form.get("hx-post") == "/budget/goals/new"
        preview = one(form, "#target-preview")
        assert preview.get("hx-get") == "/budget/goals/preview"
        assert "change" in (preview.get("hx-trigger") or "")
        assert preview.get("hx-include") == "closest form"
        rules = [
            o.get("value") for o in select(form, 'select[name="target_rule"] option')
        ]
        assert rules == ["fixed", "months_of_expenses"]
        text_ = text(
            one(
                hx.get(
                    "/budget/goals/preview",
                    params={"target_rule": "months_of_expenses", "target_factor": "3"},
                ).text,
                "#target-preview",
            )
        )
        assert "3 months" in text_ and "$" in text_

    def test_create_then_edit(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        response = client.post(
            "/budget/goals/new",
            data={
                "name": "Car",
                "target_rule": "fixed",
                "target_amount": "5,000",
                "contribution_kind": "fixed",
                "monthly_contribution": "200",
            },
        )
        assert location(response) == "/budget?tab=goals"
        page = client.get("/budget?tab=goals").text
        card = next(c for c in select(page, "[id^=goal-]") if "Car" in text(c))
        goal_id = id_of(card, "goal-")
        form = one(hx.get(f"/budget/goals/{goal_id}/edit").text, "form")
        assert one(form, 'input[name="name"]').get("value") == "Car"
        assert one(form, 'input[name="target_amount"]').get("value") == "5,000.00"
        saved = client.post(
            f"/budget/goals/{goal_id}/edit",
            data={
                "name": "Car",
                "target_rule": "fixed",
                "target_amount": "6000",
                "contribution_kind": "fixed",
                "monthly_contribution": "",
            },
        )
        card = one(saved.text, f"#goal-{goal_id}[hx-swap-oob]")
        assert "$6,000.00" in text(card)

    def test_blank_name_is_a_422(self, client: TestClient, budget: Budget) -> None:
        response = client.post(
            "/budget/goals/new",
            data={"name": " ", "target_rule": "fixed", "target_amount": "10"},
        )
        assert response.status_code == 422
        one(response.text, '[role="alert"]')

    def test_remove(self, client: TestClient, hx: TestClient, budget: Budget) -> None:
        """The confirm swaps nothing in, and the answer was empty: the card
        stayed on the page until the next load."""
        confirm = hx.get(f"/budget/goals/{budget.goal}/remove").text
        one(confirm, f'[hx-delete="/budget/goals/{budget.goal}"]')
        response = client.delete(f"/budget/goals/{budget.goal}")
        _primary, siblings = oob(response.text)
        assert {s.get("id"): s.get("hx-swap-oob") for s in siblings} == {
            f"goal-{budget.goal}": "delete",
            "budget-stats": "outerHTML",
        }
        none(client.get("/budget?tab=goals").text, f"#goal-{budget.goal}")

    def test_a_goal_write_moves_the_strip(
        self, client: TestClient, budget: Budget
    ) -> None:
        """A goal's ask is a term of the verdict, and its writes sent no
        strip: pausing one left the month's figure stale."""
        response = client.post(f"/budget/goals/{budget.goal}/pause")
        primary, siblings = oob(response.text)
        assert primary[0].get("id") == f"goal-{budget.goal}"
        assert [s.get("id") for s in siblings] == ["budget-stats"]


class TestEnvelopes:
    def test_card_and_verbs(self, client: TestClient, budget: Budget) -> None:
        page = client.get("/budget?tab=envelopes").text
        card = one(page, f"#envelope-{budget.envelope}")
        assert "Kids" in text(card) and "$50.00" in text(card)
        for verb in ("credit", "spend", "edit"):
            one(card, f'[hx-get="/budget/envelopes/{budget.envelope}/{verb}"]')

    def test_credit_and_spend_update_the_balance_in_place(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        form = one(hx.get(f"/budget/envelopes/{budget.envelope}/credit").text, "form")
        assert form.get("hx-post") == f"/budget/envelopes/{budget.envelope}/credit"
        one(form, 'input[name="note"]')
        credited = client.post(
            f"/budget/envelopes/{budget.envelope}/credit",
            data={"amount": "10", "note": "chores"},
        )
        assert "$60.00" in text(
            one(credited.text, f"#envelope-{budget.envelope}[hx-swap-oob]")
        )
        spent = client.post(
            f"/budget/envelopes/{budget.envelope}/spend", data={"amount": "25"}
        )
        assert "$35.00" in text(
            one(spent.text, f"#envelope-{budget.envelope}[hx-swap-oob]")
        )
        assert "dialog:close" in triggers(spent)

    def test_zero_amount_is_a_422(self, client: TestClient, budget: Budget) -> None:
        response = client.post(
            f"/budget/envelopes/{budget.envelope}/spend", data={"amount": "0"}
        )
        assert response.status_code == 422

    def test_create_edit_delete(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        form = one(hx.get("/budget/envelopes/new").text, "form")
        cadences = [
            o.get("value") for o in select(form, 'select[name="cadence"] option')
        ]
        assert cadences == ["weekly", "monthly"]
        created = client.post(
            "/budget/envelopes/new",
            data={
                "name": "Dog",
                "monthly_credit": "15",
                "cadence": "weekly",
                "starting_balance": "5",
            },
        )
        assert location(created) == "/budget?tab=envelopes"
        page = client.get("/budget?tab=envelopes").text
        card = next(c for c in select(page, "[id^=envelope-]") if "Dog" in text(c))
        envelope_id = id_of(card, "envelope-")
        form = one(hx.get(f"/budget/envelopes/{envelope_id}/edit").text, "form")
        assert one(form, 'input[name="monthly_credit"]').get("value") == "15.00"
        saved = client.post(
            f"/budget/envelopes/{envelope_id}/edit",
            data={"monthly_credit": "20", "cadence": "monthly", "auto_credit": "on"},
        )
        assert "+$20.00/mo" in text(
            one(saved.text, f"#envelope-{envelope_id}[hx-swap-oob]")
        )
        confirm = hx.get(f"/budget/envelopes/{envelope_id}/remove").text
        one(confirm, f'[hx-delete="/budget/envelopes/{envelope_id}"]')
        response = client.delete(f"/budget/envelopes/{envelope_id}")
        _primary, siblings = oob(response.text)
        assert {s.get("id"): s.get("hx-swap-oob") for s in siblings} == {
            f"envelope-{envelope_id}": "delete",
            "budget-stats": "outerHTML",
        }

    def test_an_envelope_write_moves_the_strip(
        self, client: TestClient, budget: Budget
    ) -> None:
        """An auto-credit is a term of the verdict too."""
        response = client.post(
            f"/budget/envelopes/{budget.envelope}/credit", data={"amount": "5"}
        )
        _primary, siblings = oob(response.text)
        assert [s.get("id") for s in siblings] == [
            f"envelope-{budget.envelope}",
            "budget-stats",
        ]

    def test_an_envelope_can_pay_for_a_tag(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        """What the household buys her, she pays for from her envelope:
        the dialog names the tag, and the card says what it pays for
        (#240)."""
        form = one(hx.get(f"/budget/envelopes/{budget.envelope}/edit").text, "form")
        one(form, 'input[name="tag"]')
        saved = client.post(
            f"/budget/envelopes/{budget.envelope}/edit",
            data={"monthly_credit": "10", "cadence": "weekly", "tag": "Kids stuff"},
        )
        card = one(saved.text, f"#envelope-{budget.envelope}[hx-swap-oob]")
        assert "pays for what is tagged Kids stuff" in text(
            one(card, "[data-pays-for]")
        )
        form = one(hx.get(f"/budget/envelopes/{budget.envelope}/edit").text, "form")
        assert one(form, 'input[name="tag"]').get("value") == "Kids stuff"

    def test_the_dialog_sets_when_it_starts_counting(
        self, client: TestClient, hx: TestClient, budget: Budget
    ) -> None:
        """Charges tagged after the fact are only paid for if counting
        starts early enough to cover them (2026-09-23)."""
        saved = client.post(
            f"/budget/envelopes/{budget.envelope}/edit",
            data={
                "monthly_credit": "10",
                "cadence": "weekly",
                "tag": "Kids stuff",
                "tag_since": "2026-08-01",
            },
        )
        card = one(saved.text, f"#envelope-{budget.envelope}[hx-swap-oob]")
        assert "from Aug 1" in text(one(card, "[data-pays-for]"))
        form = one(hx.get(f"/budget/envelopes/{budget.envelope}/edit").text, "form")
        assert (
            one(form, 'input[name="tag_since"][type="date"]').get("value")
            == "2026-08-01"
        )

    def test_unknown_envelope_is_404(self, client: TestClient, ledger: Ledger) -> None:
        assert client.get("/budget/envelopes/999999/credit").status_code == 404


class TestAPastMonth:
    """#359: page back to a month that has ended and read it as it went."""

    @staticmethod
    async def _two_months_back(
        finance: FinanceService,
        async_db_session: AsyncSession,
        budget: Budget,
        ledger: Ledger,
    ) -> int:
        """Beyond the ledger's reach (its rows are the last five days): a
        $300 fuel limit with $120 against it, $80 of groceries no limit
        covered, a $2,000 paycheck, and $500 moved to savings."""
        period = shift_period(current_period_month(), -2)
        first = date(period // 100, period % 100, 1)
        await seed_limit(
            finance, budget.fuel, 30_000, owner_user_id=None, period_month=period
        )
        for day, amount, name, category in (
            (1, 200_000, "Payroll", None),
            (5, -12_000, "Gas", budget.fuel),
            (6, -8_000, "Market", budget.groceries),
            (7, -50_000, "To savings", None),
        ):
            txn = await finance.create_transaction(
                account_id=ledger.checking,
                amount=amount,
                txn_date=first + timedelta(days=day - 1),
                name=name,
            )
            txn.category_id = category
            txn.is_transfer = name == "To savings"
            async_db_session.add(txn)
        await async_db_session.commit()
        return period

    def test_the_pager_reaches_back_six_months(
        self, client: TestClient, budget: Budget
    ) -> None:
        pager = select(client.get("/budget").text, "#month-pager a")
        labels = [text(a) for a in pager]
        now = next(i for i, label in enumerate(labels) if label.startswith("Now"))
        assert labels[now - 6 : now] == [
            calendar.month_abbr[shift_period(current_period_month(), -n) % 100]
            for n in range(6, 0, -1)
        ]
        last = pager[now - 1]
        assert last.get("href") == "/budget?month=-1&tab=limits"
        assert last.get("hx-get") == last.get("href")

    async def test_a_past_month_reads_as_it_went(
        self,
        client: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        budget: Budget,
        ledger: Ledger,
    ) -> None:
        period = await self._two_months_back(finance, async_db_session, budget, ledger)

        page = client.get("/budget?month=-2").text

        assert text(one(page, "#budget > div > h2")) == (
            f"How did {period_label(period)} go?"
        )
        assert stats(page) == {
            "Money in": "$2,000.00",
            "Money out": "$200.00",
            "Budgets": "$120.00",
            period_label(period): "+$1,800.00",
        }
        # Figures only, like a month ahead: nothing here opens today's rows.
        none(page, "#budget-stats [hx-get]")

    async def test_its_limits_are_read_only_and_open_that_month(
        self,
        client: TestClient,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        budget: Budget,
        ledger: Ledger,
    ) -> None:
        """An edit on a past month's row would have set THIS month's limit,
        so nothing on it is editable; its name opens that month's rows."""
        period = await self._two_months_back(finance, async_db_session, budget, ledger)

        page = client.get("/budget?month=-2").text

        row = one(page, "#limits li[id^=line-]")
        assert text(row).startswith("Auto:Fuel")
        assert "$120.00 of $300.00" in text(row)
        none(one(page, "#limits"), "form, input")
        none(row, '[hx-get$="/remove"]')
        none(page, '[hx-get="/budget/lines/new"]')
        none(page, "#commitments")
        opener = one(row, '[hx-get*="/transactions"]').get("hx-get")
        assert (
            opener == f"/budget/lines/{id_of(row, 'line-')}/transactions?month={period}"
        )
        body = hx.get(opener).text
        assert text(one(body, "h2").getnext()) == (
            f"$120.00 of $300.00 in {period_label(period)}"
        )
        assert [text(td) for td in select(body, "tbody td")].count("Gas") == 1
