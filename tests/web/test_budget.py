"""Budget: the month header, its limits, suggestions, goals and envelopes.

One page, four tabs on one URL. The stats strip answers "does this month
clear" and pages through the months ahead (pattern 3); every cell opens
its arithmetic in the dialog (pattern 4). Lines, suggestions, goals and
envelopes are row actions (pattern 2) and dialog forms (pattern 1); each
change ships the strip back out of band so the verdict never goes stale.
"""

import json

from fastapi.testclient import TestClient

from tests.web.conftest import Budget, Ledger
from tests.web.dom import none, one, oob, select, text, triggers


def cells(page: str) -> dict[str, str]:
    """The stats strip as ``{label: value}``."""
    return {text(dt): text(dt.getnext()) for dt in select(page, "#budget-stats dt")}


class TestPage:
    def test_tabs_and_the_strip(self, client: TestClient, budget: Budget) -> None:
        page = client.get("/budget").text
        tabs = [text(a) for a in select(page, "#budget [role=tablist] a")]
        assert tabs == ["Limits", "Suggested", "Goals (1)", "Envelopes (1)"]
        strip = cells(page)
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
        assert len(chips) == 6 and text(chips[0]).startswith("Now")
        assert chips[1].get("hx-target") == "#budget"
        ahead = client.get("/budget?month=1").text
        # A future month's verdict is titled with the month, and the cells
        # are figures, not doors: nothing to drill into yet.
        assert "This month" not in cells(ahead)
        none(ahead, "#budget-stats [hx-get]")
        assert (
            one(ahead, "#month-pager a[aria-current]").get("href") == "/budget?month=1"
        )
        assert (
            one(ahead, '#month-pager a[aria-label="Previous month"]').get("href")
            == "/budget?month=0"
        )

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
        assert "$45.00" in text(line)  # spent so far
        one(line, f'[hx-delete="/budget/lines/{budget.line}"]')

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

    def test_removing_a_line_empties_the_row_and_moves_the_strip(
        self, client: TestClient, budget: Budget
    ) -> None:
        response = client.delete(f"/budget/lines/{budget.line}")
        primary, siblings = oob(response.text)
        assert primary == [] and siblings[0].get("id") == "budget-stats"
        assert cells(client.get("/budget").text)["Budgets"] == "$0.00"

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
        assert json.loads(response.headers["HX-Location"])["path"] == "/budget"
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


class TestSuggestions:
    def test_empty_when_history_is_thin(
        self, client: TestClient, budget: Budget
    ) -> None:
        page = client.get("/budget?tab=suggested").text
        assert "Nothing to suggest" in text(one(page, "#suggestions"))

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

        assert int(spent / allocated * 100) == 79
        assert budget_line_status(allocated, spent) == "good"
        assert budget_line_status(allocated, 16_000) == "warn"

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
        assert (
            json.loads(response.headers["HX-Location"])["path"] == "/budget?tab=goals"
        )
        page = client.get("/budget?tab=goals").text
        card = next(c for c in select(page, "[id^=goal-]") if "Car" in text(c))
        goal_id = (card.get("id") or "").removeprefix("goal-")
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
        confirm = hx.get(f"/budget/goals/{budget.goal}/remove").text
        one(confirm, f'[hx-delete="/budget/goals/{budget.goal}"]')
        response = client.delete(f"/budget/goals/{budget.goal}")
        assert response.status_code == 200 and response.text == ""
        none(client.get("/budget?tab=goals").text, f"#goal-{budget.goal}")


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
        assert (
            json.loads(created.headers["HX-Location"])["path"]
            == "/budget?tab=envelopes"
        )
        page = client.get("/budget?tab=envelopes").text
        card = next(c for c in select(page, "[id^=envelope-]") if "Dog" in text(c))
        envelope_id = (card.get("id") or "").removeprefix("envelope-")
        form = one(hx.get(f"/budget/envelopes/{envelope_id}/edit").text, "form")
        assert one(form, 'input[name="monthly_credit"]').get("value") == "15.00"
        saved = client.post(
            f"/budget/envelopes/{envelope_id}/edit",
            data={"monthly_credit": "20", "cadence": "monthly", "auto_credit": "on"},
        )
        assert "+$20.00/mo" in text(
            one(saved.text, f"#envelope-{envelope_id}[hx-swap-oob]")
        )
        response = client.delete(f"/budget/envelopes/{envelope_id}")
        assert response.status_code == 200 and response.text == ""

    def test_unknown_envelope_is_404(self, client: TestClient, ledger: Ledger) -> None:
        assert client.get("/budget/envelopes/999999/credit").status_code == 404
