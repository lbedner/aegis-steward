"""Filters that exclude, take several values, and an amount range (#326).

One select per axis said what to show and never what not to: "groceries
but not Costco" had no answer. Each axis now takes several values, or
everything but them; an amount range narrows by magnitude; and a filter
set can be kept under a name.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, table_rows, text

REGISTER = "/accounts/all"


def names(markup: str) -> list[str]:
    return [text(row["Payee"]) for row in table_rows(markup, "#register table")]


def _box(page: str, axis: str, label: str) -> str:
    """The value of ``label``'s box in the ``axis`` checklist."""
    for row in select(page, f"#register-{axis} label"):
        if text(row) == label:
            return one(row, "input").get("value") or ""
    raise AssertionError(f"no {label} in {axis}")


class TestSeveralValuesOrAllBut:
    @pytest.mark.queryspy(threshold=3)  # the page, then the pick
    def test_two_categories_show_both(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get(REGISTER).text
        groceries = _box(page, "categories", "Food:Groceries")
        fuel = _box(page, "categories", "Auto:Fuel")

        both = client.get(f"{REGISTER}?categories={groceries}&categories={fuel}").text

        assert sorted(names(both)) == ["Gas", "Market", "Market"]

    @pytest.mark.queryspy(threshold=4)  # the page, the pick, its fragment
    def test_all_but_a_category_keeps_the_uncategorized(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        """A bare NOT IN drops every row with no category along with the
        one left out."""
        groceries = _box(client.get(REGISTER).text, "categories", "Food:Groceries")
        url = f"{REGISTER}?categories={groceries}&exclude=categories"

        page = client.get(url).text

        assert sorted(names(page)) == ["Gas", "Mystery charge", "Payroll"]
        switch = one(page, '#register-categories input[name="exclude"]')
        assert switch.get("value") == "categories"
        assert switch.get("checked") is not None
        # The same narrowing as a fragment: the filter state is the URL.
        assert sorted(names(hx.get(url).text)) == ["Gas", "Mystery charge", "Payroll"]

    @pytest.mark.queryspy(threshold=3)  # two registers
    def test_an_account_can_be_picked_on_every_account_only(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        card = client.get(f"{REGISTER}?accounts={ledger.card}").text

        assert sorted(names(card)) == ["Gas", "Mystery charge"]
        # One account's register is already one account.
        none(client.get(f"/accounts/{ledger.checking}").text, "#register-accounts")


class TestAnAmountRange:
    def test_by_magnitude_in_dollars(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get(f"{REGISTER}?amount_min=15&amount_max=$20").text

        assert sorted(names(page)) == ["Gas", "Market"]
        assert one(page, 'input[name="amount_min"]').get("value") == "15"


class TestSavedViews:
    @pytest.mark.queryspy(threshold=4)  # the menu, the save, the removal
    def test_a_view_is_the_register_as_it_was_saved(
        self, hx: TestClient, ledger: Ledger
    ) -> None:
        here = f"http://testserver{REGISTER}?accounts={ledger.card}"

        none(hx.get("/accounts/views").text, "#register-views-menu li")
        saved = hx.post(
            "/accounts/views", data={"name": "Card"}, headers={"HX-Current-URL": here}
        ).text

        link = one(saved, "#register-views-menu li a")
        assert text(link) == "Card"
        assert link.get("href") == f"{REGISTER}?accounts={ledger.card}"
        assert link.get("hx-get") == link.get("href")

        view = one(saved, "#register-views-menu li").get("id")
        gone = hx.delete(f"/accounts/views/{view.removeprefix('view-')}").text
        none(gone, "#register-views-menu li")

    @pytest.mark.queryspy(threshold=3)  # each refusal redraws the menu
    def test_a_view_needs_a_name_and_a_register(self, hx: TestClient) -> None:
        unnamed = hx.post(
            "/accounts/views",
            data={"name": " "},
            headers={"HX-Current-URL": f"http://testserver{REGISTER}"},
        )
        elsewhere = hx.post(
            "/accounts/views",
            data={"name": "Bills"},
            headers={"HX-Current-URL": "http://testserver/bills"},
        )

        assert (unnamed.status_code, elsewhere.status_code) == (422, 422)
        assert "Name the view." in unnamed.text

    def test_the_register_loads_its_menu_once(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        menu = one(client.get(REGISTER).text, "#register-views")
        assert menu.get("hx-preserve") is not None
        assert one(menu, "[hx-get]").get("hx-get") == "/accounts/views"
