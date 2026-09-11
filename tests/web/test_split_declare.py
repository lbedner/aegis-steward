"""Splits and declare-recurring: two more register actions through the
dialog, answering with rows out of band."""

from fastapi.testclient import TestClient

from tests.web.conftest import REGISTER, Ledger
from tests.web.dom import none, one, select, text, triggers
from tests.web.test_row_actions import category_id, txn_id


class TestSplit:
    def test_menu_dialog_and_split(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        market = txn_id(page, "Market")
        one(page, f'#txn-{market} [hx-get="/transactions/{market}/split"]')
        food = category_id(page, "Food:Groceries")

        dialog = hx.get(f"/transactions/{market}/split").text
        form = one(dialog, "form")
        assert form.get("hx-post") == f"/transactions/{market}/split"
        assert len(select(form, 'input[name="amount"]')) >= 2
        assert len(select(form, 'select[name="category_id"]')) >= 2

        response = client.post(
            f"/transactions/{market}/split",
            data={
                "amount": ["10.00", "5.00"],
                "category_id": [food, ""],
                "memo": ["produce", ""],
            },
        )
        assert response.status_code == 200
        row = one(response.text, f"tr#txn-{market}")
        lines = select(row, ".split-line")
        assert len(lines) == 3  # two stated parts plus the remainder
        assert "$10.00" in text(lines[0]) and "produce" in text(lines[0])
        assert "dialog:close" in triggers(response)
        one(row, f'[hx-delete="/transactions/{market}/split"]')

    def test_unsplit_returns_the_plain_row(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        market = txn_id(page, "Market")
        client.post(
            f"/transactions/{market}/split",
            data={"amount": ["10.00"], "category_id": [""], "memo": [""]},
        )
        response = client.delete(f"/transactions/{market}/split")
        row = one(response.text, f"tr#txn-{market}")
        none(row, ".split-line")

    def test_parts_over_the_total_are_a_422(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        market = txn_id(page, "Market")  # -$30.00
        response = client.post(
            f"/transactions/{market}/split",
            data={"amount": ["40.00"], "category_id": [""], "memo": [""]},
        )
        assert response.status_code == 422
        one(response.text, '[role="alert"]')


class TestDeclareRecurring:
    def test_preview_then_declare(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(REGISTER).text
        markets = [
            (tr.get("id") or "").removeprefix("txn-")
            for tr in select(page, "#register tbody tr")
            if text(one(tr, '[data-cell="payee"]')).startswith("Market")
        ]
        bar = one(page, "#bulk-actions")
        assert (
            one(bar, '[hx-get="/transactions/declare"]').get("hx-target")
            == "#dialog-body"
        )

        dialog = hx.get(
            "/transactions/declare", params={"transaction_ids": markets}
        ).text
        form = one(dialog, "form")
        assert form.get("hx-post") == "/transactions/declare"
        entry = one(form, ".plan-entry")
        name_input = one(entry, 'input[name^="name:"]')
        assert name_input.get("value")
        assert "$22.50" in text(entry)  # average of 30.00 and 15.00
        hidden = select(form, 'input[name="transaction_ids"]')
        assert sorted(h.get("value") for h in hidden) == sorted(markets)

        response = client.post(
            "/transactions/declare",
            data={
                "transaction_ids": markets,
                name_input.get("name") or "": "Grocery run",
            },
        )
        assert response.status_code == 200
        fired = triggers(response)
        assert "dialog:close" in fired and "1" in fired["toast"]["text"]
        assert len(select(response.text, "tr[hx-swap-oob]")) == 2
        names = [
            s["name"] for s in client.get("/api/v1/finance/recurring").json()["items"]
        ]
        assert "Grocery run" in names

    def test_a_single_payment_still_previews_or_explains(
        self, hx: TestClient, ledger: Ledger
    ) -> None:
        """One payment is a plan of one; if the API ever refuses, the
        reason is the dialog's content rather than a JSON error."""
        page = hx.get(REGISTER).text
        payroll = txn_id(page, "Payroll")
        dialog = hx.get(
            "/transactions/declare", params={"transaction_ids": [payroll]}
        ).text
        assert select(dialog, ".plan-entry") or select(dialog, "#dialog-note")
