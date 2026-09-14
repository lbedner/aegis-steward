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


@pytest.fixture
async def brokerage(finance: FinanceService, async_db_session: AsyncSession) -> int:
    account = await finance.create_manual_account(
        name="M1 Finance", account_type="brokerage", classification="asset"
    )
    assert account.id is not None
    await async_db_session.commit()
    return account.id


class TestPropertyDetails:
    def test_menu_offers_property_items_on_a_property(
        self, client: TestClient, house: int
    ) -> None:
        from app.services.finance.constants import ACCOUNT_ACTION_LABELS as LABELS

        page = client.get(f"/accounts/{house}").text
        labels = [text(li) for li in select(page, "#manage-menu li")]
        assert labels == [
            LABELS[key]
            for key in (
                "rename",
                "institution",
                "reconcile",
                "property",
                "valuations",
                "remove",
            )
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


class TestPositionsByHand:
    """A manual investment account has no sync to fill it, so the only way
    its holdings arrive is typed. One box for one position or twenty,
    because that is how positions are read off a statement."""

    def test_a_manual_brokerage_offers_positions_and_a_synced_one_does_not(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """A connected account's holdings are rewritten by the next sync,
        so a hand-typed row there would read as data loss."""
        from app.services.finance.constants import account_actions

        assert "positions" in account_actions(
            account_type="brokerage", classification="asset", is_manual=True
        )
        assert "positions" not in account_actions(
            account_type="brokerage", classification="asset", is_manual=False
        )
        assert "positions" not in account_actions(
            account_type="checking", classification="asset", is_manual=True
        )

    def test_a_paste_files_every_line(
        self, client: TestClient, brokerage: int
    ) -> None:
        account = brokerage

        client.post(
            f"/accounts/{account}/positions",
            data={
                "as_of_date": "2026-09-14",
                "positions": "VOO 14.2 $512.30\nSCHD 60 28.11\nQQQM 3.4 214.90",
            },
        )

        rows = client.get(f"/accounts/{account}").text
        held = [text(cell) for cell in select(rows, "#holdings tbody td")]
        assert "VOO" in held and "SCHD" in held and "QQQM" in held

    def test_one_bad_line_files_nothing(
        self, client: TestClient, brokerage: int
    ) -> None:
        """Filing eighteen of twenty leaves the account wrong in a way
        that looks right, and nothing says which two are missing."""
        account = brokerage

        body = client.post(
            f"/accounts/{account}/positions",
            data={
                "as_of_date": "2026-09-14",
                "positions": "VOO 14.2 512.30\nSCHD lots 28.11",
            },
        ).text

        assert "not a quantity" in body
        rows = client.get(f"/accounts/{account}").text
        assert "VOO" not in text(one(rows, "#holdings"))


class TestTermsByHand:
    """The terms of a debt, typed. One declaration renders the form,
    parses it back, writes it through the executor the approval card
    uses, and labels the row on that card - so a term added to the shape
    appears in all four with no edit to any of them."""

    @pytest.fixture
    def card(self, client: TestClient, ledger: Ledger) -> int:
        return ledger.card

    def test_the_shape_decides_which_questions_are_asked(
        self, client: TestClient, card: int
    ) -> None:
        """A card has no origination date and no term in months; a loan
        has both. Neither list is written in a template."""
        from app.services.finance.domains.writes.terms import shape_for

        on_card = {term.name for term in shape_for("credit_card")}
        on_loan = {term.name for term in shape_for("loan")}
        assert "origination_date" not in on_card
        assert {"origination_date", "loan_term_months"} <= on_loan
        assert on_card < on_loan

        body = client.get(f"/accounts/{card}/terms").text
        named = {el.get("name") for el in select(body, "form [name]")}
        assert on_card <= named
        assert "origination_date" not in named

    def test_a_rate_is_typed_as_a_rate_and_stored_as_basis_points(
        self, client: TestClient, card: int
    ) -> None:
        """7.99 on the statement, 799 in the column, "7.99%" on the card.
        The parse and the display are one pair in one place."""
        client.post(
            f"/accounts/{card}/terms",
            data={
                "outstanding_balance": "$7,857.27",
                "interest_rate_bps": "21.49",
                "minimum_payment_amount": "224.57",
                "next_payment_due_date": "2026-10-03",
            },
        )

        body = client.get(f"/accounts/{card}/terms").text
        values = {
            el.get("name"): el.get("value") for el in select(body, "form input[name]")
        }
        assert values["interest_rate_bps"] == "21.49"
        # Grouped on the way out, ungrouped on the way back in:
        # ``money_to_cents`` takes the comma out, which is why a figure
        # copied off a statement pastes in without editing.
        assert values["outstanding_balance"] == "7,857.27"
        assert values["next_payment_due_date"] == "2026-10-03"

    def test_a_bad_rate_says_so_and_writes_nothing(
        self, client: TestClient, card: int
    ) -> None:
        body = client.post(
            f"/accounts/{card}/terms",
            data={"interest_rate_bps": "about seven"},
        ).text

        assert "is not a rate" in body
        again = client.get(f"/accounts/{card}/terms").text
        values = {
            el.get("name"): el.get("value") for el in select(again, "form input[name]")
        }
        assert not values["interest_rate_bps"]


class TestADialogLeavesYouWhereItFoundYou:
    """Saving used to land you wherever the route happened to name.
    Editing a document from the Documents tab dropped you on Overview;
    renaming from anywhere dropped you on the register. Each dialog had
    picked its own answer to a question htmx was already answering with
    every request."""

    def test_the_answer_comes_from_the_page_you_were_on(self) -> None:
        from starlette.requests import Request

        from app.components.web_frontend.rendering import where_from

        def asking_from(url: str | None) -> Request:
            headers = [(b"hx-current-url", url.encode())] if url else []
            return Request(
                {"type": "http", "headers": headers, "method": "POST", "path": "/x"}
            )

        here = "http://localhost:8000/accounts/51/documents"
        assert where_from(asking_from(here), "/fallback") == "/accounts/51/documents"

        # The query is part of where you were: a filtered register is a
        # different place from an unfiltered one.
        filtered = "http://localhost:8000/accounts/all?q=shoprite&page=3"
        assert where_from(asking_from(filtered), "/fallback") == (
            "/accounts/all?q=shoprite&page=3"
        )

        # No header (a plain form post, a test client): the route's own
        # answer stands.
        assert where_from(asking_from(None), "/fallback") == "/fallback"

        # A redirect target read off a header is not handed to a browser
        # unexamined.
        assert where_from(asking_from("javascript:alert(1)"), "/fallback") == (
            "/fallback"
        )
