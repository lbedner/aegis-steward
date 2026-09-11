"""The Accounts section, split in two.

``/accounts`` is the portfolio: every account by group, with balances.
``/accounts/all`` and ``/accounts/{id}`` are registers, each the full
width of the content area, and the switcher in a register's header is
how you move between them — one control, opened when you want it, in
place of a column that was always there.
"""

from fastapi.testclient import TestClient

from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, text


def groups(page: str) -> list[str]:
    return [text(h.getchildren()[0]) for h in select(page, "#portfolio h2")]


class TestPortfolio:
    def test_shows_the_empty_state_and_a_zero_total(self, client: TestClient) -> None:
        page = client.get("/accounts").text
        assert "No accounts yet" in text(one(page, "#portfolio"))
        assert "$0.00" in text(one(page, "[data-net-worth]"))

    def test_groups_in_the_ledger_order_with_subtotals(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts").text
        assert groups(page) == ["Banking", "Credit Cards"]
        banking = select(page, "#portfolio section")[0]
        assert "$150.00" in text(one(banking, "h2"))
        # Largest balance first within a group.
        assert [text(one(a, "[data-name]")) for a in select(banking, "a")] == [
            "Checking",
            "Savings",
        ]

    def test_the_header_carries_the_grand_total(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        assert "$125.00" in text(one(client.get("/accounts").text, "[data-net-worth]"))

    def test_a_row_opens_that_account_s_register(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """The portfolio's job is to hand you off; each row is a door to
        one register, navigated the way any section is."""
        row = one(
            client.get("/accounts").text,
            f'#portfolio a[href="/accounts/{ledger.card}"]',
        )
        assert row.get("hx-get") == row.get("href")
        assert row.get("hx-target") == "#app-content"
        assert row.get("hx-push-url") == "true"

    def test_liability_row_shows_the_statement_line_when_present(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        row = one(
            client.get("/accounts").text,
            f'#portfolio a[href="/accounts/{ledger.card}"]',
        )
        assert "-$25.00" in text(row)

    def test_the_portfolio_carries_no_register(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Two jobs, two pages: this one never renders a transaction."""
        none(client.get("/accounts").text, "#register")

    def test_fragment_has_no_shell(self, hx: TestClient, ledger: Ledger) -> None:
        fragment = hx.get("/accounts").text
        none(fragment, "aside#sidebar")
        one(fragment, "#portfolio")


class TestRegister:
    def test_every_account_at_once(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get("/accounts/all").text
        one(page, "#register")
        assert text(one(page, "#account-switcher summary")) == "All accounts"
        assert "$125.00" in text(one(page, "[data-balance]"))

    def test_one_account(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        header = one(page, "#account-detail header")
        assert text(one(header, "#account-switcher summary")) == "Checking"
        assert "CHECKING" in text(header)
        assert "$100.00" in text(one(header, "[data-balance]"))

    def test_the_switcher_lists_every_account_by_group(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """What the sidebar used to say, on demand: the same groups, the
        same balances, and the one you are on marked."""
        switcher = one(client.get(f"/accounts/{ledger.card}").text, "#account-switcher")
        assert [text(h) for h in select(switcher, "[data-group]")] == [
            "Banking",
            "Credit Cards",
        ]
        links = {a.get("href"): text(a) for a in select(switcher, "a")}
        assert links["/accounts/all"].startswith("All accounts")
        assert "$100.00" in links[f"/accounts/{ledger.checking}"]
        current = one(switcher, 'a[aria-current="page"]')
        assert current.get("href") == f"/accounts/{ledger.card}"

    def test_manage_menu_follows_the_account_kind(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Rename and Reconcile always; Secured by on a debt; Remove only on
        a manual account (a provider account belongs to its connection)."""
        card = client.get(f"/accounts/{ledger.card}").text
        assert [text(li) for li in select(card, "#manage-menu li")] == [
            "Rename",
            "Reconcile",
            "Secured by",
            "Remove",
        ]
        checking = client.get(f"/accounts/{ledger.checking}").text
        assert [text(li) for li in select(checking, "#manage-menu li")] == [
            "Rename",
            "Reconcile",
            "Remove",
        ]

    def test_all_accounts_offers_no_manage_menu(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        none(client.get("/accounts/all").text, "#manage-menu")

    def test_import_carries_the_account_it_is_opened_from(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        assert (
            one(page, "header button[hx-get^='/accounts/import']").get("hx-get")
            == f"/accounts/import?account_id={ledger.checking}"
        )

    def test_fragment_has_no_shell(self, hx: TestClient, ledger: Ledger) -> None:
        fragment = hx.get(f"/accounts/{ledger.card}").text
        none(fragment, "aside#sidebar")
        one(fragment, "#register")

    def test_unknown_account_is_404(self, client: TestClient) -> None:
        assert client.get("/accounts/999999").status_code == 404
