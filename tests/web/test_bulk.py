"""Bulk actions on the register: select rows, act on the selection.

Selection is plain checkboxes named ``transaction_ids``; each action
includes the checked ones. Responses swap nothing at the trigger and
send every touched row out of band, so one contract serves single and
bulk alike.
"""

import json

from fastapi.testclient import TestClient

from tests.web.conftest import Ledger
from tests.web.dom import none, one, oob, select, text
from tests.web.test_row_actions import payee_cell, txn_id


def payee_of(row) -> str:  # noqa: ANN001
    return text(payee_cell(row)).split(" ")[0]


class TestSelection:
    def test_every_row_has_a_checkbox(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get("/accounts").text
        boxes = select(
            page, '#register tbody input[type="checkbox"][name="transaction_ids"]'
        )
        assert len(boxes) == 5
        assert text(select(page, "#register thead th")[0]) == "Select"

    def test_action_bar_acts_on_the_checked_rows(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        bar = one(client.get("/accounts").text, "#bulk-actions")
        include = "[name='transaction_ids']:checked"
        payee = one(bar, '[hx-get="/transactions/payee"]')
        assert (
            payee.get("hx-include") == include
            and payee.get("hx-target") == "#dialog-body"
        )
        tag = one(bar, '[hx-get="/transactions/tag"]')
        assert (
            tag.get("hx-include") == include and tag.get("hx-target") == "#dialog-body"
        )
        remove = one(bar, '[hx-post="/transactions/delete"]')
        assert remove.get("hx-include") == include
        assert remove.get("hx-swap") == "none" and remove.get("hx-confirm")


class TestBulkDelete:
    def test_deletes_the_selection_out_of_band(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts").text
        ids = [txn_id(page, "Gas"), txn_id(page, "Mystery charge")]
        response = client.post("/transactions/delete", data={"transaction_ids": ids})
        primary, siblings = oob(response.text)
        assert primary == []
        deleted = [s for s in siblings if s.get("hx-swap-oob") == "delete"]
        assert sorted(s.get("id") for s in deleted) == sorted(f"txn-{i}" for i in ids)
        assert text(one(response.text, "#uncategorized-count")) == "1 uncategorized"
        after = client.get("/accounts").text
        none(after, f"#txn-{ids[0]}")
        none(after, f"#txn-{ids[1]}")


class TestAssignPayee:
    def test_dialog_carries_the_selection_and_the_choices(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts").text
        ids = [txn_id(page, "Gas"), txn_id(page, "Payroll")]
        dialog = hx.get("/transactions/payee", params={"transaction_ids": ids}).text
        form = one(dialog, "form")
        assert form.get("hx-post") == "/transactions/payee"
        assert form.get("hx-target") == "#dialog-body"
        hidden = select(form, 'input[name="transaction_ids"]')
        assert sorted(h.get("value") for h in hidden) == sorted(ids)
        one(form, 'select[name="merchant_id"]')
        one(form, 'input[name="new_name"]')
        one(form, 'select[name="category_id"]')

    def test_new_payee_is_created_and_rows_come_back_out_of_band(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts").text
        gas = txn_id(page, "Gas")
        response = client.post(
            "/transactions/payee", data={"transaction_ids": [gas], "new_name": "Shell"}
        )
        primary, siblings = oob(response.text)
        row = one(response.text, f"tr#txn-{gas}")
        assert row.get("hx-swap-oob") == "outerHTML"
        assert payee_of(row) == "Shell"
        assert "dialog:close" in json.loads(response.headers["HX-Trigger"])

    def test_offers_the_similar_rows_after_a_single_assignment(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Two payee-less "Market" rows: naming one offers the other, a
        suggestion the user confirms, never applied on its own."""
        page = client.get("/accounts").text
        markets = [
            (tr.get("id") or "").removeprefix("txn-")
            for tr in select(page, "#register tbody tr")
            if text(payee_cell(tr)) == "Market"
        ]
        first, other = markets
        response = client.post(
            "/transactions/payee",
            data={"transaction_ids": [first], "new_name": "Market Inc"},
        )
        assert "dialog:close" not in response.headers.get("HX-Trigger", "")
        offer = one(response.text, "form#similar-offer")
        assert "1 similar transaction" in text(one(response.text, "p"))
        assert [
            h.get("value") for h in select(offer, 'input[name="transaction_ids"]')
        ] == [other]
        merchant = one(offer, 'input[name="merchant_id"]').get("value")

        accepted = client.post(
            "/transactions/payee",
            data={"transaction_ids": [other], "merchant_id": merchant},
        )
        assert payee_of(one(accepted.text, f"tr#txn-{other}")) == "Market"
        assert "dialog:close" in json.loads(accepted.headers["HX-Trigger"])

    def test_nothing_chosen_is_a_422(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get("/accounts").text
        gas = txn_id(page, "Gas")
        response = client.post("/transactions/payee", data={"transaction_ids": [gas]})
        assert response.status_code == 422
        one(response.text, '[role="alert"]')


class TestBulkTag:
    def test_tags_the_selection(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get("/accounts").text
        ids = [txn_id(page, "Gas"), txn_id(page, "Payroll")]
        response = client.post(
            "/transactions/tag", data={"transaction_ids": ids, "name": "audit"}
        )
        for i in ids:
            row = one(response.text, f"tr#txn-{i}")
            assert row.get("hx-swap-oob") == "outerHTML"
            assert text(one(row, ".tag")).startswith("audit")
