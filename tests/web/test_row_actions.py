"""Row actions on the register (pattern 2) and the out-of-band contract.

An action posts against one transaction and gets that row back as
``outerHTML``, plus out-of-band siblings for anything else the change
moved: here the uncategorised count. Tests split the response with the
kit's ``oob()`` so the row and the counters are asserted separately.
"""

import json

from fastapi.testclient import TestClient

from tests.web.conftest import REGISTER, Ledger
from tests.web.dom import none, one, oob, select, text, triggers


def category_id(page: str, name: str) -> str:
    option = next(
        o
        for o in select(page, '#register-filters select[name="category_id"] option')
        if text(o) == name
    )
    return option.get("value") or ""


def txn_id(page: str, payee: str) -> str:
    for tr in select(page, "#register tbody tr"):
        if text(payee_cell(tr)).split(" ")[0] == payee.split(" ")[0] and text(
            payee_cell(tr)
        ).startswith(payee):
            return (tr.get("id") or "").removeprefix("txn-")
    raise AssertionError(payee)


def payee_cell(tr):  # noqa: ANN001, ANN201
    """The payee cell: the one holding the row's tag chips slot."""
    return next(td for td in tr.getchildren() if td.get("data-cell") == "payee")


class TestRowMenu:
    def test_offers_the_same_verbs_as_the_selection_bar(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """A single row gets what a selection gets, Set payee included:
        picking one row to name its payee is the common case."""
        page = client.get(f"/accounts/{ledger.card}").text
        gas = txn_id(page, "Gas")
        items = [text(li) for li in select(page, f"#txn-{gas} [role=menu] li")]
        assert items == ["Set payee", "Tag", "Split", "Make recurring", "Remove"]
        payee = one(page, f"#txn-{gas} [hx-get^='/transactions/payee']")
        assert payee.get("hx-get") == f"/transactions/payee?transaction_ids={gas}"
        assert payee.get("hx-target") == "#dialog-body"

        # The combined register answers with rows that name their account,
        # so its menu asks the dialog to do the same.
        combined = client.get(REGISTER).text
        assert (
            one(combined, f"#txn-{gas} [hx-get^='/transactions/payee']").get("hx-get")
            == f"/transactions/payee?transaction_ids={gas}&show_account=true"
        )

    def test_the_dialog_it_opens_names_that_one_transaction(
        self, hx: TestClient, client: TestClient, ledger: Ledger
    ) -> None:
        gas = txn_id(client.get(f"/accounts/{ledger.card}").text, "Gas")
        dialog = hx.get(f"/transactions/payee?transaction_ids={gas}").text
        assert "Gas" in text(one(dialog, "h2"))
        assert {
            i.get("value") for i in select(dialog, 'input[name="transaction_ids"]')
        } == {gas}


class TestRowMarkup:
    def test_category_cell_is_a_select_that_posts_on_change(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        mystery = txn_id(page, "Mystery charge")
        cell = one(page, f"#txn-{mystery} select[name='category_id']")
        assert cell.get("hx-post") == f"/transactions/{mystery}/categorize"
        assert cell.get("hx-trigger") == "change"
        assert cell.get("hx-target") == "closest tr"
        assert cell.get("hx-swap") == "outerHTML"
        assert one(cell, "option[selected]").get("value") == ""

    def test_categorised_row_preselects_its_category(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        gas = txn_id(page, "Gas")
        selected = one(page, f"#txn-{gas} select option[selected]")
        assert text(selected) == "Auto:Fuel"

    def test_uncategorised_count_is_shown(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(REGISTER).text
        assert text(one(page, "#uncategorized-count")) == "2 uncategorized"

    def test_row_menu_offers_tag_and_delete(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        gas = txn_id(page, "Gas")
        row = one(page, f"#txn-{gas}")
        tag = one(row, f'[hx-get="/transactions/tag?transaction_ids={gas}"]')
        assert tag.get("hx-target") == "#dialog-body"
        delete = one(row, f'[hx-delete="/transactions/{gas}"]')
        assert delete.get("hx-target") == "closest tr"
        assert delete.get("hx-swap") == "outerHTML"
        assert delete.get("hx-confirm")


class TestCategorize:
    def test_returns_the_row_and_the_new_count_out_of_band(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        mystery = txn_id(page, "Mystery charge")
        food = category_id(page, "Food:Groceries")

        response = client.post(
            f"/transactions/{mystery}/categorize", data={"category_id": food}
        )
        assert response.status_code == 200
        primary, siblings = oob(response.text)
        assert len(primary) == 1 and primary[0].tag == "tr"
        assert primary[0].get("id") == f"txn-{mystery}"
        assert text(one(primary[0], "select option[selected]")) == "Food:Groceries"
        assert [s.get("id") for s in siblings] == ["uncategorized-count"]
        assert text(siblings[0]) == "1 uncategorized"

    def test_blank_category_clears_it(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        gas = txn_id(page, "Gas")
        response = client.post(
            f"/transactions/{gas}/categorize", data={"category_id": ""}
        )
        primary, siblings = oob(response.text)
        assert one(primary[0], "select option[selected]").get("value") == ""
        assert text(siblings[0]) == "3 uncategorized"

    def test_unknown_transaction_is_404(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        assert (
            client.post(
                "/transactions/999999/categorize", data={"category_id": ""}
            ).status_code
            == 404
        )


class TestTags:
    def test_tag_dialog_then_post_adds_a_chip_to_the_row(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        gas = txn_id(page, "Gas")

        dialog = hx.get(f"/transactions/tag?transaction_ids={gas}").text
        none(dialog, "html")
        picker = one(dialog, "#tag-picker")
        assert one(picker, "input[type=search]").get("placeholder") == (
            "Search or name a tag"
        )
        form = one(picker, "form[hx-post='/transactions/tag']")
        assert one(form, 'input[name="transaction_ids"]').get("value") == gas

        response = client.post(
            "/transactions/tag", data={"transaction_ids": [gas], "name": "trip"}
        )
        _, siblings = oob(response.text)
        row = one(response.text, f"tr#txn-{gas}")
        assert row.get("hx-swap-oob") == "outerHTML"
        chip = one(row, ".tag")
        assert text(chip).startswith("trip")
        remove = one(chip, "[hx-delete]")
        assert remove.get("hx-delete", "").startswith(f"/transactions/{gas}/tags/")
        # The dialog closes on success.
        assert triggers(response)["dialog:close"] is None

    def test_the_tag_picker_refuses_a_tag_the_row_already_wears(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        """Same rule as the payee: a tag already on every selected row
        would write nothing, so it cannot be chosen again."""
        page = client.get(f"/accounts/{ledger.card}").text
        gas = txn_id(page, "Gas")
        client.post(
            "/transactions/tag", data={"transaction_ids": [gas], "name": "trip"}
        )

        picker = one(
            hx.get(f"/transactions/tag?transaction_ids={gas}").text, "#tag-picker"
        )
        current = one(picker, 'button[aria-current="true"]')
        assert current.get("value") == "trip"
        assert current.get("disabled") is not None

    def test_untag_returns_the_row_without_the_chip(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        gas = txn_id(page, "Gas")
        tagged = client.post(
            "/transactions/tag", data={"transaction_ids": [gas], "name": "trip"}
        ).text
        remove = one(tagged, ".tag [hx-delete]").get("hx-delete") or ""

        response = client.delete(remove)
        primary, _ = oob(response.text)
        none(primary[0], ".tag")

    def test_blank_tag_name_re_renders_the_form_with_a_422(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        gas = txn_id(page, "Gas")
        response = client.post(
            "/transactions/tag", data={"transaction_ids": [gas], "name": "  "}
        )
        assert response.status_code == 422
        one(response.text, '[role="alert"]')
        one(response.text, "#tag-picker")


class TestDelete:
    def test_removes_the_row_and_updates_the_count(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        mystery = txn_id(page, "Mystery charge")
        response = client.delete(f"/transactions/{mystery}")
        assert response.status_code == 200
        primary, siblings = oob(response.text)
        assert primary == []
        assert text(one(response.text, "#uncategorized-count")) == "1 uncategorized"
        assert json.loads(response.headers["HX-Trigger"])["toast"]["text"]
        after = client.get(f"/accounts/{ledger.card}").text
        none(after, f"#txn-{mystery}")

    def test_unknown_transaction_is_404(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        assert client.delete("/transactions/999999").status_code == 404


class TestOobCounter:
    def test_the_count_chip_keeps_its_style_when_re_sent(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """The out-of-band chip replaces the styled one in the filter bar,
        so it has to carry the same classes or the bar changes size."""
        page = client.get(f"/accounts/{ledger.card}").text
        mystery = txn_id(page, "Mystery charge")
        response = client.post(
            f"/transactions/{mystery}/categorize", data={"category_id": ""}
        )
        _rows, siblings = oob(response.text)
        chip = next(s for s in siblings if s.get("id") == "uncategorized-count")
        assert "text-xxs" in (chip.get("class") or "")
