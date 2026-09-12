"""Manage menu: rename, reconcile (preview then apply), remove.

Each opens in the dialog; success closes it and navigates the content
area back to the account (or to the list after a removal)."""

from fastapi.testclient import TestClient

from app.services.finance.utils import current_date
from tests.web.conftest import Ledger
from tests.web.dom import location, none, one, select, text, triggers


class TestMenu:
    def test_items_open_their_dialogs(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        urls = [b.get("hx-get") for b in select(page, "#manage-menu button")]
        assert urls[:3] == [
            f"/accounts/{ledger.card}/rename",
            f"/accounts/{ledger.card}/institution",
            f"/accounts/{ledger.card}/reconcile",
        ]
        assert urls[-1] == f"/accounts/{ledger.card}/remove"


class TestRename:
    def test_form_then_rename(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        form = one(hx.get(f"/accounts/{ledger.checking}/rename").text, "form")
        assert form.get("hx-post") == f"/accounts/{ledger.checking}/rename"
        assert one(form, 'input[name="name"]').get("value") == "Checking"

        response = client.post(
            f"/accounts/{ledger.checking}/rename", data={"name": "Main Checking"}
        )
        assert location(response) == f"/accounts/{ledger.checking}"
        assert "dialog:close" in triggers(response)
        page = client.get(f"/accounts/{ledger.checking}").text
        assert text(one(page, "#account-switcher summary")) == "Main Checking"

    def test_blank_name_is_a_422(self, client: TestClient, ledger: Ledger) -> None:
        response = client.post(
            f"/accounts/{ledger.checking}/rename", data={"name": "  "}
        )
        assert response.status_code == 422
        one(response.text, '[role="alert"]')


class TestReconcile:
    def test_preview_shows_the_delta_then_apply_lands_it(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        form = one(hx.get(f"/accounts/{ledger.checking}/reconcile").text, "form")
        assert form.get("hx-post") == f"/accounts/{ledger.checking}/reconcile"
        assert (
            one(form, 'input[name="statement_date"]').get("value")
            == current_date().isoformat()
        )
        one(form, 'input[name="statement_balance"]')
        one(form, 'button[name="preview"]')

        preview = client.post(
            f"/accounts/{ledger.checking}/reconcile",
            data={
                "statement_date": current_date().isoformat(),
                "statement_balance": "120",
                "preview": "1",
            },
        )
        assert preview.status_code == 200
        assert "HX-Location" not in preview.headers
        # The register sums the ledger's transactions ($455.00: payroll less
        # groceries); the statement says $120.00; the difference is what
        # an adjustment would absorb.
        delta = one(preview.text, "#reconcile-delta")
        assert "$455.00" in text(delta) and "-$335.00" in text(delta)
        apply = one(preview.text, 'button[name="apply"]')
        assert apply is not None

        applied = client.post(
            f"/accounts/{ledger.checking}/reconcile",
            data={
                "statement_date": current_date().isoformat(),
                "statement_balance": "120",
            },
        )
        assert location(applied) == f"/accounts/{ledger.checking}"
        # One transfer-flagged adjustment absorbs the difference: hidden by
        # default, visible with the transfers toggle.
        page = client.get(f"/accounts/{ledger.checking}?include_transfers=on").text
        assert len(select(page, "#register tbody tr")) == 4

    def test_bad_balance_is_a_422(self, client: TestClient, ledger: Ledger) -> None:
        response = client.post(
            f"/accounts/{ledger.checking}/reconcile",
            data={
                "statement_date": current_date().isoformat(),
                "statement_balance": "??",
                "preview": "1",
            },
        )
        assert response.status_code == 422
        one(response.text, '[role="alert"]')


class TestRemove:
    def test_confirm_then_remove(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        confirm = hx.get(f"/accounts/{ledger.savings}/remove").text
        button = one(confirm, f'[hx-delete="/accounts/{ledger.savings}"]')
        assert button is not None
        assert "Savings" in text(one(confirm, "h2"))

        response = client.delete(f"/accounts/{ledger.savings}")
        assert location(response) == "/accounts"
        assert "dialog:close" in triggers(response)
        none(
            client.get("/accounts").text,
            f'#portfolio a[href="/accounts/{ledger.savings}"]',
        )

    def test_unknown_account_is_404(self, client: TestClient) -> None:
        assert client.delete("/accounts/999999").status_code == 404
        assert client.get("/accounts/999999/rename").status_code == 404
