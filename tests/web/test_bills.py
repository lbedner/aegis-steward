"""Bills & Income: the streams table, its row actions, and the dialogs.

The page partitions streams into Bills, Income and Detected (one URL,
``?tab=``), re-renders the table in place (pattern 3) and answers every
row action with the row (pattern 2). The editor, pause, categorize and
match dialogs are pattern 4 around pattern 1.
"""

from datetime import date, timedelta
import json

from fastapi.testclient import TestClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from tests.web.conftest import Streams
from tests.web.dom import none, one, oob, select, table_rows, text, triggers

HEALTH = "Health"


def rows_by_name(page: str) -> dict[str, dict]:
    return {text(cells["Name"]): cells for cells in table_rows(page, "#streams table")}


def menu(page: str, stream_id: int) -> list[str]:
    return [text(li) for li in select(page, f"#stream-{stream_id} [role=menu] li")]


class TestPage:
    def test_tabs_carry_counts_and_the_default_is_bills(
        self, client: TestClient, streams: Streams
    ) -> None:
        page = client.get("/bills").text
        tabs = [text(a) for a in select(page, "#streams [role=tablist] a")]
        assert tabs == ["Bills (2)", "Income (1)", "Detected (1)"]
        current = one(page, '#streams [role=tablist] a[aria-current="page"]')
        assert text(current) == "Bills (2)"
        rows = rows_by_name(page)
        assert list(rows) == ["Water", "Rent"]  # soonest due first
        rent = rows["Rent"]
        assert text(rent["Account"]) == "Checking"
        assert text(rent["Amount"]) == "$1,500.00"
        assert text(rent["Cadence"]) == "Monthly"
        assert text(rent[HEALTH]) == "Active"

    def test_health_reads_overdue_past_the_due_date(
        self, client: TestClient, streams: Streams
    ) -> None:
        water = rows_by_name(client.get("/bills").text)["Water"]
        badge = one(water[HEALTH], "[data-tone]")
        assert text(badge) == "Overdue" and badge.get("data-tone") == "warn"

    def test_income_and_detected_tabs(
        self, client: TestClient, streams: Streams
    ) -> None:
        assert list(rows_by_name(client.get("/bills?tab=income").text)) == ["Payroll"]
        detected = client.get("/bills?tab=detected").text
        rows = rows_by_name(detected)
        assert list(rows) == ["Netflix"]
        assert text(one(rows["Netflix"]["Status"], "[data-tone]")) == "Detected"
        assert "Confirm" in menu(detected, streams.netflix)

    def test_search_narrows_within_the_tab(
        self, client: TestClient, streams: Streams
    ) -> None:
        assert list(rows_by_name(client.get("/bills?q=rent").text)) == ["Rent"]

    def test_filters_replace_the_table_in_place(
        self, client: TestClient, streams: Streams
    ) -> None:
        page = client.get("/bills").text
        form = one(page, "form#stream-filters")
        assert form.get("hx-target") == form.get("hx-select") == "#streams"
        assert form.get("hx-swap") == "outerHTML"
        for tab in select(page, "#streams [role=tablist] a"):
            assert tab.get("hx-target") == "#streams"
        assert "$1,545.00" in text(one(page, "#monthly-cost"))  # rent + water

    async def test_window_chips_filter_by_last_activity(
        self,
        client: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        streams: Streams,
    ) -> None:
        """A bill that last paid before the window drops out; one that has
        never paid stays, because the window has nothing to judge it by."""
        page = client.get("/bills").text
        assert (
            one(page, "#stream-filters input[name='days'][checked]").get("value")
            == "9999"
        )
        assert set(rows_by_name(page)) == {"Rent", "Water"}

        rent = await finance.get_recurring(streams.rent, None)
        assert rent is not None
        rent.last_date = date.today() - timedelta(days=200)
        async_db_session.add(rent)
        await async_db_session.commit()

        narrowed = client.get("/bills?days=30").text
        assert set(rows_by_name(narrowed)) == {"Water"}  # rent last paid too long ago
        assert "Bills (1)" in [
            text(a) for a in select(narrowed, "#streams [role=tablist] a")
        ]

    def test_tabs_carry_the_window_and_the_search(
        self, client: TestClient, streams: Streams
    ) -> None:
        page = client.get("/bills?days=30&q=rent").text
        income = next(
            a
            for a in select(page, "#streams [role=tablist] a")
            if text(a).startswith("Income")
        )
        assert "days=30" in (income.get("href") or "")
        assert "q=rent" in (income.get("href") or "")

    def test_fragment_has_no_shell(self, hx: TestClient, streams: Streams) -> None:
        fragment = hx.get("/bills").text
        none(fragment, "html")
        one(fragment, "#streams")

    def test_empty_ledger_says_so(self, client: TestClient) -> None:
        page = client.get("/bills").text
        assert "No bills yet" in text(one(page, "#streams"))


class TestRescan:
    def test_button_and_re_render(self, client: TestClient, streams: Streams) -> None:
        page = client.get("/bills").text
        button = one(page, '[hx-post="/bills/rescan"]')
        assert button.get("hx-target") == "#streams"
        assert button.get("hx-include") == "#stream-filters"
        response = client.post("/bills/rescan", data={"tab": "income", "q": ""})
        assert response.status_code == 200
        assert list(rows_by_name(response.text)) == ["Payroll"]
        assert "Rescan" in json.loads(response.headers["HX-Trigger"])["toast"]["text"]


class TestRowActions:
    def test_menu_offers_the_verbs_for_the_state(
        self, client: TestClient, streams: Streams
    ) -> None:
        page = client.get("/bills").text
        assert menu(page, streams.rent) == [
            "Edit",
            "Categorize",
            "Match a payment",
            "Pause",
            "Mute",
            "Delete",
        ]
        income = client.get("/bills?tab=income").text
        assert "Match a payment" not in menu(income, streams.payroll)

    def test_confirm_swaps_the_row_back_curated(
        self, client: TestClient, streams: Streams
    ) -> None:
        page = client.get("/bills?tab=detected").text
        button = one(page, f'#stream-{streams.netflix} [hx-post$="/confirm"]')
        assert button.get("hx-target") == "closest tr"
        assert button.get("hx-swap") == "outerHTML"
        response = client.post(f"/bills/{streams.netflix}/confirm")
        row = one(response.text, f"tr#stream-{streams.netflix}")
        assert row.get("hx-swap-oob") is None
        assert "Confirm" not in menu(response.text, streams.netflix)
        assert text(select(row, "[data-tone]")[-1]) == "Good"  # the Status cell

    def test_mute_then_unmute(self, client: TestClient, streams: Streams) -> None:
        muted = client.post(f"/bills/{streams.rent}/mute").text
        assert text(select(muted, "[data-tone]")[-1]) == "Muted"
        assert "Unmute" in menu(muted, streams.rent)
        unmuted = client.post(f"/bills/{streams.rent}/unmute").text
        assert "Mute" in menu(unmuted, streams.rent)

    def test_pause_dialog_then_resume(
        self, client: TestClient, hx: TestClient, streams: Streams
    ) -> None:
        dialog = hx.get(f"/bills/{streams.rent}/pause").text
        form = one(dialog, "form")
        assert form.get("hx-post") == f"/bills/{streams.rent}/pause"
        until = one(form, 'input[name="until"]')
        assert until.get("value")  # a quick pick is prefilled
        one(form, 'textarea[name="note"]')

        response = client.post(
            f"/bills/{streams.rent}/pause",
            data={
                "until": (date.today() + timedelta(days=60)).isoformat(),
                "note": "moving",
            },
        )
        assert response.status_code == 200
        rows, _ = oob(response.text)
        assert rows == []  # the row travels out of band; the dialog closes
        row = one(response.text, f"tr#stream-{streams.rent}[hx-swap-oob]")
        assert text(select(row, "[data-tone]")[-1]) == "Paused"
        assert "dialog:close" in triggers(response)
        assert "Resume" in menu(response.text, streams.rent)

        resumed = client.post(f"/bills/{streams.rent}/resume").text
        assert "Pause" in menu(resumed, streams.rent)

    def test_pause_without_a_date_is_a_422(
        self, client: TestClient, streams: Streams
    ) -> None:
        response = client.post(f"/bills/{streams.rent}/pause", data={"until": ""})
        assert response.status_code == 422
        one(response.text, '[role="alert"]')

    def test_delete_removes_the_row(self, client: TestClient, streams: Streams) -> None:
        page = client.get("/bills").text
        button = one(
            page, f'#stream-{streams.rent} [hx-delete="/bills/{streams.rent}"]'
        )
        assert button.get("hx-target") == "closest tr"
        assert button.get("hx-confirm")
        response = client.delete(f"/bills/{streams.rent}")
        assert response.status_code == 200 and response.text == ""
        assert "Rent" not in rows_by_name(client.get("/bills").text)

    def test_unknown_stream_is_404(self, client: TestClient, streams: Streams) -> None:
        assert client.post("/bills/999999/mute").status_code == 404
        assert client.get("/bills/999999/edit").status_code == 404


class TestCategorize:
    def test_dialog_then_row(
        self, client: TestClient, hx: TestClient, streams: Streams
    ) -> None:
        dialog = hx.get(f"/bills/{streams.rent}/categorize").text
        form = one(dialog, "form")
        options = select(form, 'select[name="category_id"] option')
        groceries = next(o for o in options if text(o) == "Food:Groceries")
        response = client.post(
            f"/bills/{streams.rent}/categorize",
            data={"category_id": groceries.get("value") or ""},
        )
        row = one(response.text, f"tr#stream-{streams.rent}[hx-swap-oob]")
        assert "Food:Groceries" in text(row)
        assert "dialog:close" in triggers(response)


class TestEditor:
    def test_add_form_then_create(
        self, client: TestClient, hx: TestClient, streams: Streams
    ) -> None:
        button = one(client.get("/bills").text, '[hx-get="/bills/new"]')
        assert button.get("hx-target") == "#dialog-body"
        form = one(hx.get("/bills/new").text, "form")
        assert form.get("hx-post") == "/bills/new"
        kinds = [
            o.get("value") for o in select(form, 'select[name="direction"] option')
        ]
        assert kinds == ["outflow", "inflow"]
        cadences = [
            o.get("value") for o in select(form, 'select[name="frequency"] option')
        ]
        assert cadences[0] == "weekly" and cadences[-1] == "once"
        one(form, 'input[name="expected_amount"]')
        one(form, 'input[name="next_expected_date"]')
        one(form, 'select[name="account_id"]')

        response = client.post(
            "/bills/new",
            data={
                "name": "Gym",
                "direction": "outflow",
                "frequency": "monthly",
                "expected_amount": "40",
                "next_expected_date": (date.today() + timedelta(days=5)).isoformat(),
                "account_id": "",
            },
        )
        location = json.loads(response.headers["HX-Location"])
        assert location["path"] == "/bills?tab=bills"
        assert "dialog:close" in triggers(response)
        assert "Gym" in rows_by_name(client.get("/bills").text)

    def test_add_validates(self, client: TestClient, streams: Streams) -> None:
        response = client.post(
            "/bills/new",
            data={
                "name": " ",
                "direction": "outflow",
                "frequency": "monthly",
                "expected_amount": "lots",
                "next_expected_date": "",
            },
        )
        assert response.status_code == 422
        errors = text(one(response.text, '[role="alert"]')).lower()
        assert "name" in errors and "amount" in errors and "date" in errors

    def test_edit_prefills_and_swaps_the_row(
        self, client: TestClient, hx: TestClient, streams: Streams
    ) -> None:
        form = one(hx.get(f"/bills/{streams.rent}/edit").text, "form")
        assert form.get("hx-post") == f"/bills/{streams.rent}/edit"
        assert one(form, 'input[name="name"]').get("value") == "Rent"
        none(form, 'select[name="direction"]')  # the kind is not editable
        assert one(form, 'input[name="expected_amount"]').get("value") == "1,500.00"
        assert (
            one(form, 'select[name="frequency"] option[selected]').get("value")
            == "monthly"
        )

        response = client.post(
            f"/bills/{streams.rent}/edit",
            data={
                "name": "Rent (new place)",
                "frequency": "monthly",
                "expected_amount": "1,650",
                "next_expected_date": (date.today() + timedelta(days=12)).isoformat(),
                "account_id": "",
            },
        )
        row = one(response.text, f"tr#stream-{streams.rent}[hx-swap-oob]")
        assert "Rent (new place)" in text(row) and "$1,650.00" in text(row)


class TestMatch:
    def test_dialog_lists_candidates_and_attaching_updates_the_row(
        self, client: TestClient, hx: TestClient, streams: Streams
    ) -> None:
        before = rows_by_name(client.get("/bills").text)["Water"]
        assert text(before[HEALTH]) == "Overdue"

        dialog = hx.get(f"/bills/{streams.water}/match").text
        assert "Water" in text(one(dialog, "h2"))
        candidates = select(dialog, "#match-candidates li")
        assert len(candidates) == 1
        assert "WATER CO" in text(candidates[0]) and "$46.00" in text(candidates[0])
        pick = one(candidates[0], "button[hx-post]")
        assert pick.get("hx-post") == f"/bills/{streams.water}/match"
        transaction_id = json.loads(pick.get("hx-vals") or "{}")["transaction_id"]

        response = client.post(
            f"/bills/{streams.water}/match", data={"transaction_id": transaction_id}
        )
        row = one(response.text, f"tr#stream-{streams.water}[hx-swap-oob]")
        assert text(select(row, "[data-tone]")[0]) == "Active"  # the Health cell
        fired = triggers(response)
        assert "dialog:close" in fired and "Matched" in fired["toast"]["text"]
        after = rows_by_name(client.get("/bills").text)["Water"]
        assert text(after["Next due"]) != text(before["Next due"])

    def test_no_candidates_says_so(self, hx: TestClient, streams: Streams) -> None:
        dialog = hx.get(f"/bills/{streams.rent}/match").text
        none(dialog, "#match-candidates li")
        assert "No unclaimed transactions" in text(one(dialog, "#dialog-note"))

    def test_review_walks_the_overdue_bills(
        self, client: TestClient, hx: TestClient, streams: Streams
    ) -> None:
        page = client.get("/bills").text
        review = one(page, '[hx-get="/bills/review"]')
        assert (
            text(review) == "Review (1)" and review.get("hx-target") == "#dialog-body"
        )

        dialog = hx.get("/bills/review").text
        assert "1 of 1" in text(one(dialog, "h2"))
        pick = one(dialog, "#match-candidates button[hx-post]")
        vals = json.loads(pick.get("hx-vals") or "{}")
        response = client.post(f"/bills/{streams.water}/match", data=vals)
        # Last in the queue: the dialog closes rather than advancing.
        assert "dialog:close" in triggers(response)
        none(client.get("/bills").text, '[hx-get="/bills/review"]')
