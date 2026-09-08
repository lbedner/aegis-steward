"""The Accounts page: grouped account list on the left, the selected
account's header on the right.

Selecting an account is a URL (``/accounts/{id}``); the list link swaps
the detail column and refreshes the list highlight from the same
response, so one route serves the cold load and the click.
"""

from fastapi.testclient import TestClient

from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, text


def group_headers(page: str) -> list[str]:
    return [text(h.getchildren()[0]) for h in select(page, "#accounts-list h2")]


class TestEmptyLedger:
    def test_shows_the_empty_state_and_a_zero_total(self, client: TestClient) -> None:
        page = client.get("/accounts").text
        assert "No accounts yet" in text(one(page, "#accounts-list"))
        assert text(one(page, "#account-detail header h2")) == "All accounts"
        assert "$0.00" in text(one(page, "#account-detail"))


class TestAccountList:
    def test_groups_in_the_ledger_order_with_subtotals(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts").text
        assert group_headers(page) == ["Banking", "Credit Cards"]
        banking = select(page, "#accounts-list section")[0]
        assert "$150.00" in text(one(banking, "h2"))
        # Largest balance first within a group.
        assert [text(a.getchildren()[0]) for a in select(banking, "a")] == [
            "Checking",
            "Savings",
        ]

    def test_all_accounts_row_carries_the_grand_total(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        row = one(client.get("/accounts").text, '#accounts-list a[href="/accounts"]')
        assert "$125.00" in text(row)

    def test_links_swap_the_detail_and_refresh_the_list(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """One response feeds both columns: hx-select takes the detail,
        hx-select-oob re-takes the list so the highlight follows."""
        page = client.get("/accounts").text
        link = one(page, f'#accounts-list a[href="/accounts/{ledger.card}"]')
        assert link.get("hx-get") == link.get("href")
        assert link.get("hx-target") == "#account-detail"
        assert link.get("hx-select") == "#account-detail"
        assert link.get("hx-select-oob") == "#accounts-list"
        assert link.get("hx-push-url") == "true"

    def test_liability_row_shows_the_statement_line_when_present(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        row = one(
            client.get("/accounts").text,
            f'#accounts-list a[href="/accounts/{ledger.card}"]',
        )
        assert "-$25.00" in text(row)


class TestSelectedAccount:
    def test_header_names_the_account_and_marks_it_current(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        header = one(page, "#account-detail header")
        assert text(one(header, "h2")) == "Checking"
        assert "CHECKING" in text(header)
        assert "$100.00" in text(header)
        current = one(page, '#accounts-list a[aria-current="page"]')
        assert current.get("href") == f"/accounts/{ledger.checking}"

    def test_manage_menu_follows_the_account_kind(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Rename and Reconcile always; Secured by on a debt; Remove only on
        a manual account (a provider account belongs to its connection)."""
        card = client.get(f"/accounts/{ledger.card}").text
        assert [
            text(li) for li in select(card, "#account-detail header [role=menu] li")
        ] == [
            "Rename",
            "Reconcile",
            "Secured by",
            "Remove",
        ]
        checking = client.get(f"/accounts/{ledger.checking}").text
        assert [
            text(li) for li in select(checking, "#account-detail header [role=menu] li")
        ] == [
            "Rename",
            "Reconcile",
            "Remove",
        ]

    def test_manage_menu_is_a_native_disclosure(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        details = one(page, "#account-detail header details")
        assert text(one(details, "summary")) == "Manage"

    def test_fragment_has_no_shell(self, hx: TestClient, ledger: Ledger) -> None:
        fragment = hx.get(f"/accounts/{ledger.card}").text
        none(fragment, "aside")
        one(fragment, "#accounts-list")
        one(fragment, "#account-detail")

    def test_unknown_account_is_404(self, client: TestClient) -> None:
        assert client.get("/accounts/999999").status_code == 404
