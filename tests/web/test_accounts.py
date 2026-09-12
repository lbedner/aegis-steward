"""The Accounts section, split in two.

``/accounts`` is the portfolio: every account by group, with balances.
``/accounts/all`` and ``/accounts/{id}`` are registers, each the full
width of the content area, and the switcher in a register's header is
how you move between them — one control, opened when you want it, in
place of a column that was always there.
"""

from fastapi.testclient import TestClient
import pytest

from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, table_rows, text, triggers


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
        """Rename, Institution and Reconcile always; Secured by on a
        debt; Remove only on a manual account (a provider account belongs
        to its connection). The labels come from the service beside the
        rule that orders them, so this menu and Flet's cannot drift."""
        card = client.get(f"/accounts/{ledger.card}").text
        assert [text(li) for li in select(card, "#manage-menu li")] == [
            "Rename",
            "Institution",
            "Reconcile",
            "Secured by",
            "Remove",
        ]
        checking = client.get(f"/accounts/{ledger.checking}").text
        assert [text(li) for li in select(checking, "#manage-menu li")] == [
            "Rename",
            "Institution",
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


class TestTheInstitutionAnAccountIsHeldAt:
    """Naming a bank is the same gesture as naming a payee: the same
    picker, search or type, and creating happens on the way through.
    Nobody should have to go and make an institution first."""

    def _open(self, hx: TestClient, account_id: int) -> str:
        return hx.get(f"/accounts/{account_id}/institution").text

    def test_the_dialog_is_the_house_picker(
        self, hx: TestClient, ledger: Ledger
    ) -> None:
        picker = one(self._open(hx, ledger.checking), "#institution-picker")
        assert one(picker, "input[type=search]").get("placeholder") == (
            "Search or name a bank"
        )
        form = one(picker, f"form[hx-post='/accounts/{ledger.checking}/institution']")
        assert one(form, "button[name=new_name]") is not None

    def test_typing_a_name_makes_it_and_sets_it(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        response = client.post(
            f"/accounts/{ledger.checking}/institution", data={"new_name": "Fidelity"}
        )
        assert "dialog:close" in triggers(response)

        again = self._open(hx, ledger.checking)
        assert "Fidelity" in text(one(again, "#institution-picker-options"))

    def test_the_next_account_opens_on_the_last_one_used(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        """Three brokerage accounts at one bank is one decision, not
        three."""
        client.post(
            f"/accounts/{ledger.checking}/institution", data={"new_name": "Fidelity"}
        )

        other = one(self._open(hx, ledger.savings), "#institution-picker-options")
        chosen = [
            b
            for b in select(other, "button[name=institution_id]")
            if b.get("disabled") is not None
        ]
        assert [text(b).strip().startswith("Fidelity") for b in chosen] == [True]

    def test_it_can_be_taken_off(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        client.post(
            f"/accounts/{ledger.checking}/institution", data={"new_name": "Fidelity"}
        )
        opened = self._open(hx, ledger.checking)
        assert "No institution" in text(
            one(opened, "form[hx-post] button[type=submit]:not([name])")
        )

        response = client.post(
            f"/accounts/{ledger.checking}/institution", data={"institution_id": ""}
        )
        assert "dialog:close" in triggers(response)


class TestEveryAccountCarriesAMark:
    """The portfolio should read at a glance before anybody names a bank.

    Three tiers, the same ones every other row in this app uses: the
    logo of the institution it is held at, else the glyph for what kind
    of account it is, else the initial letter.
    """

    def test_an_account_with_no_bank_still_shows_its_kind(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts").text
        row = _portfolio_row(page, "Checking")
        assert row.find(".//*[@data-glyph]") is not None

    @pytest.mark.asyncio
    async def test_naming_the_bank_puts_its_logo_there_instead(
        self, client: TestClient, ledger: Ledger, async_db_session
    ) -> None:
        from app.services.finance.domains.ledger import merchant_icon
        from app.services.finance.models import FinanceIcon

        # The resolver never fetches inside a request; an icon it has not
        # got yet is filled in the background and the row falls back
        # meanwhile. Seed the one this account will ask for.
        async_db_session.add(FinanceIcon(domain="fidelity.com", icon_b64="x"))
        await async_db_session.commit()
        merchant_icon._CACHE.clear()
        client.post(
            f"/accounts/{ledger.checking}/institution", data={"new_name": "Fidelity"}
        )
        page = client.get("/accounts").text
        row = _portfolio_row(page, "Checking")
        img = row.find(".//img")
        assert img is not None and "/icons?key=" in (img.get("src") or "")


def _portfolio_row(page: str, name: str):  # noqa: ANN202
    """The portfolio row for one account, by the name it shows."""
    for cell in select(page, "#portfolio [data-name]"):
        if text(cell).strip() == name:
            return cell.getparent().getparent()
    raise AssertionError(f"no portfolio row named {name!r}")


class TestTheInstitutionsSettingsTab:
    """Where the "how do I get in touch" answer is kept and edited.

    One row per bank, what it is worth across your accounts, and how
    many accounts sit behind it - so a duplicate ("Chase" and "Chase
    Bank") is visible rather than discovered later.
    """

    def _seed(self, client: TestClient, ledger: Ledger) -> None:
        client.post(
            f"/accounts/{ledger.checking}/institution", data={"new_name": "Fidelity"}
        )

    def test_it_is_a_settings_tab(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get("/settings/institutions").text
        labels = [text(a) for a in select(page, "#settings-nav a")]
        assert "Institutions" in labels

    def test_it_lists_each_bank_and_what_uses_it(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        self._seed(client, ledger)
        row = one(client.get("/settings/institutions").text, "#institutions tbody tr")
        assert "Fidelity" in text(row)
        assert "1" in text(row)

    def test_nothing_named_yet_says_so(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/settings/institutions").text
        assert "No institutions yet" in text(one(page, "#institutions"))

    def test_the_details_dialog_edits_how_to_reach_them(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        self._seed(client, ledger)
        row = one(client.get("/settings/institutions").text, "#institutions tbody tr")
        bank = row.get("id").removeprefix("institution-")

        form = one(hx.get(f"/settings/institutions/{bank}").text, "form")
        assert one(form, "input[name=url]") is not None
        assert one(form, "input[name=phone]") is not None

        response = client.post(
            f"/settings/institutions/{bank}",
            data={
                "name": "Fidelity",
                "url": "https://fidelity.com",
                "phone": "800-343-3548",
            },
        )
        assert "dialog:close" in triggers(response)

        # Read the cell by its column and compare it whole. Asking
        # whether a hostname appears SOMEWHERE in a row says nothing
        # about where, which is both a weak assertion and the shape
        # CodeQL objects to (py/incomplete-url-substring-sanitization).
        row = table_rows(
            client.get("/settings/institutions").text, "#institutions table"
        )[0]
        assert text(row["Website"]).strip() == "fidelity.com"


class TestTheDialogSuggestsWhatItCanSee:
    """An account called TOTAL CHECKING (CHASE) says who it is with, and
    the ledger already knows Chase as a payee with a working logo. Offer
    it rather than making someone type what is on the screen.

    Only a whole-word match on a payee you actually have, so it cannot
    invent a bank: "ROTH IRA" suggests nothing, which is correct - that
    account's institution is not in its name.
    """

    @pytest.mark.asyncio
    async def test_it_offers_a_payee_named_in_the_account(
        self, hx: TestClient, ledger: Ledger, finance, async_db_session
    ) -> None:
        await finance.create_merchant("Chase", owner_user_id=None)
        await async_db_session.commit()
        account = await finance.create_manual_account(
            name="TOTAL CHECKING (CHASE)",
            account_type="checking",
            classification="asset",
            owner_user_id=None,
        )
        await async_db_session.commit()

        dialog = hx.get(f"/accounts/{account.id}/institution").text
        suggestion = one(dialog, "[data-suggestion]")
        assert text(suggestion).strip().startswith("Chase")
        assert one(suggestion, "button[name=new_name]").get("value") == "Chase"

    @pytest.mark.asyncio
    async def test_a_name_with_no_bank_in_it_suggests_nothing(
        self, hx: TestClient, ledger: Ledger, finance, async_db_session
    ) -> None:
        await finance.create_merchant("Chase", owner_user_id=None)
        account = await finance.create_manual_account(
            name="ROTH IRA",
            account_type="brokerage",
            classification="asset",
            owner_user_id=None,
        )
        await async_db_session.commit()

        none(hx.get(f"/accounts/{account.id}/institution").text, "[data-suggestion]")
