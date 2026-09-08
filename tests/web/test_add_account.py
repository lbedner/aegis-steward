"""Add account (pattern 1 inside pattern 4): a form in the dialog that
re-renders on a 422 and, on success, closes and navigates the content
area to the new account."""

import json

from fastapi.testclient import TestClient

from app.services.finance.constants import ADD_ACCOUNT_TYPES
from tests.web.dom import none, one, select, text


class TestDialog:
    def test_button_opens_the_form_in_the_dialog(self, client: TestClient) -> None:
        button = one(client.get("/accounts").text, '[hx-get="/accounts/new"]')
        assert button.get("hx-target") == "#dialog-body"

    def test_form_offers_the_curated_types(self, hx: TestClient) -> None:
        dialog = hx.get("/accounts/new").text
        none(dialog, "html")
        form = one(dialog, "form")
        assert form.get("hx-post") == "/accounts/new"
        assert form.get("hx-target") == "#dialog-body"
        one(form, 'input[name="name"]')
        one(form, 'input[name="opening_balance"]')
        labels = [text(o) for o in select(form, 'select[name="account_type"] option')]
        assert labels == [label for _key, label in ADD_ACCOUNT_TYPES]


class TestCreate:
    def test_creates_the_account_and_navigates_to_it(self, client: TestClient) -> None:
        response = client.post(
            "/accounts/new",
            data={
                "name": "Ally Savings",
                "account_type": "savings",
                "opening_balance": "1,250.50",
            },
        )
        assert response.status_code == 200
        location = json.loads(response.headers["HX-Location"])
        assert location["target"] == "#app-content"
        assert location["path"].startswith("/accounts/")
        # Plain HX-Trigger on purpose: a navigating response is followed,
        # not swapped, so an after-settle close would never fire.
        fired = json.loads(response.headers["HX-Trigger"])
        assert "dialog:close" in fired and fired["toast"]["text"]

        page = client.get(location["path"]).text
        assert text(one(page, "#account-detail header h2")) == "Ally Savings"
        assert "$1,250.50" in text(one(page, "#account-detail header"))

    def test_a_debt_type_is_a_liability_owed(self, client: TestClient) -> None:
        response = client.post(
            "/accounts/new",
            data={
                "name": "Amex",
                "account_type": "credit_card",
                "opening_balance": "300",
            },
        )
        path = json.loads(response.headers["HX-Location"])["path"]
        page = client.get(path).text
        assert "-$300.00" in text(one(page, "#account-detail header"))
        assert "Credit Cards" in text(one(page, "#accounts-list"))

    def test_blank_name_re_renders_with_a_422(self, client: TestClient) -> None:
        response = client.post(
            "/accounts/new",
            data={"name": " ", "account_type": "checking", "opening_balance": ""},
        )
        assert response.status_code == 422
        one(response.text, '[role="alert"]')
        assert (
            one(response.text, 'select[name="account_type"] option[selected]').get(
                "value"
            )
            == "checking"
        )

    def test_unparseable_balance_is_a_422(self, client: TestClient) -> None:
        response = client.post(
            "/accounts/new",
            data={"name": "X", "account_type": "checking", "opening_balance": "lots"},
        )
        assert response.status_code == 422
        assert "balance" in text(one(response.text, '[role="alert"]')).lower()

    def test_unknown_type_is_a_422(self, client: TestClient) -> None:
        response = client.post(
            "/accounts/new",
            data={"name": "X", "account_type": "yacht", "opening_balance": ""},
        )
        assert response.status_code == 422
