"""Property details, pasted valuations, and the lien link.

Manage-menu dialogs on property and debt accounts; each posts back to
itself, re-renders on a 422, and reopens the account on success."""

import json

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from tests.web.conftest import Ledger
from tests.web.dom import location, one, select, text


@pytest.fixture
async def house(finance: FinanceService, async_db_session: AsyncSession) -> int:
    account = await finance.create_manual_account(
        name="Home", account_type="property", classification="asset"
    )
    assert account.id is not None
    await async_db_session.commit()
    return account.id


class TestPropertyDetails:
    def test_menu_offers_property_items_on_a_property(
        self, client: TestClient, house: int
    ) -> None:
        page = client.get(f"/accounts/{house}").text
        labels = [text(li) for li in select(page, "#manage-menu li")]
        assert labels == [
            "Rename",
            "Reconcile",
            "Property details",
            "Valuation history",
            "Remove",
        ]

    def test_form_then_save(
        self, client: TestClient, hx: TestClient, house: int
    ) -> None:
        form = one(hx.get(f"/accounts/{house}/property").text, "form")
        assert form.get("hx-post") == f"/accounts/{house}/property"
        kinds = [
            o.get("value") for o in select(form, 'select[name="property_kind"] option')
        ]
        assert "primary" in kinds and "rental" in kinds
        one(form, 'select[name="valuation_source"]')
        one(form, 'input[name="purchase_price"]')
        one(form, 'input[name="purchase_date"]')

        response = client.post(
            f"/accounts/{house}/property",
            data={
                "property_kind": "rental",
                "valuation_source": "user",
                "purchase_price": "350,000",
                "purchase_date": "2020-05-01",
                "down_payment": "70000",
                "address_label": "12 Oak St",
            },
        )
        assert location(response) == f"/accounts/{house}"
        again = one(hx.get(f"/accounts/{house}/property").text, "form")
        assert (
            one(again, 'select[name="property_kind"] option[selected]').get("value")
            == "rental"
        )
        assert one(again, 'input[name="purchase_price"]').get("value") == "350,000.00"
        assert one(again, 'input[name="address_label"]').get("value") == "12 Oak St"

    def test_bad_money_is_a_422(self, client: TestClient, house: int) -> None:
        response = client.post(
            f"/accounts/{house}/property",
            data={
                "property_kind": "primary",
                "valuation_source": "user",
                "purchase_price": "a lot",
            },
        )
        assert response.status_code == 422
        one(response.text, '[role="alert"]')


class TestValuations:
    def test_paste_a_series_then_see_the_history(
        self, client: TestClient, hx: TestClient, house: int
    ) -> None:
        dialog = hx.get(f"/accounts/{house}/valuations").text
        form = one(dialog, "form")
        assert form.get("hx-post") == f"/accounts/{house}/valuations"
        one(form, 'textarea[name="text"]')
        assert "No valuations yet" in text(one(dialog, "#valuation-history"))

        response = client.post(
            f"/accounts/{house}/valuations",
            data={
                "text": "2026-01-01\t400,000\n2026-06-01\t415,000",
                "source": "manual",
            },
        )
        assert response.status_code == 200
        history = one(response.text, "#valuation-history")
        rows = select(history, "tbody tr")
        assert len(rows) == 2
        assert "$415,000.00" in text(rows[0])
        assert json.loads(response.headers["HX-Trigger"])["toast"]["text"].startswith(
            "Added 2"
        )

    def test_unparseable_paste_is_a_422(self, client: TestClient, house: int) -> None:
        response = client.post(
            f"/accounts/{house}/valuations",
            data={"text": "nothing here", "source": "manual"},
        )
        assert response.status_code == 422
        one(response.text, '[role="alert"]')


class TestSecuredBy:
    def test_picker_lists_properties_and_links(
        self, client: TestClient, hx: TestClient, ledger: Ledger, house: int
    ) -> None:
        form = one(hx.get(f"/accounts/{ledger.card}/secured_by").text, "form")
        assert form.get("hx-post") == f"/accounts/{ledger.card}/secured_by"
        options = [
            text(o) for o in select(form, 'select[name="secured_by_account_id"] option')
        ]
        assert options == ["Not secured", "Home"]
        one(form, 'input[name="lien_position"]')

        response = client.post(
            f"/accounts/{ledger.card}/secured_by",
            data={"secured_by_account_id": str(house), "lien_position": "1"},
        )
        assert location(response) == f"/accounts/{ledger.card}"
        again = one(hx.get(f"/accounts/{ledger.card}/secured_by").text, "form")
        assert one(again, 'select[name="secured_by_account_id"] option[selected]').get(
            "value"
        ) == str(house)

    def test_without_a_property_the_picker_says_so(
        self, hx: TestClient, ledger: Ledger
    ) -> None:
        dialog = hx.get(f"/accounts/{ledger.card}/secured_by").text
        assert "Add a property account first" in text(one(dialog, "#dialog-note"))

    def test_lien_position_must_be_a_number(
        self, client: TestClient, ledger: Ledger, house: int
    ) -> None:
        response = client.post(
            f"/accounts/{ledger.card}/secured_by",
            data={"secured_by_account_id": str(house), "lien_position": "first"},
        )
        assert response.status_code == 422
