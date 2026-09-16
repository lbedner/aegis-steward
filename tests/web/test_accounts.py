"""The Accounts section, split in two.

``/accounts`` is the portfolio: every account by group, with balances.
``/accounts/all`` and ``/accounts/{id}`` are registers, each the full
width of the content area, and the switcher in a register's header is
how you move between them — one control, opened when you want it, in
place of a column that was always there.
"""

import json

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from tests.services._finance_factories import seed_account
from tests.web.conftest import Ledger
from tests.web.dom import none, one, portfolio_row, select, table_rows, text, triggers


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

    def test_a_row_opens_that_account(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """The portfolio's job is to hand you off; each row is a door to
        one account, navigated the way any section is - and it opens on
        the cover sheet, because the first question about an account is
        what it IS, not what happened in it."""
        row = one(
            client.get("/accounts").text,
            f'#portfolio a[href="/accounts/{ledger.card}/overview"]',
        )
        assert row.get("hx-get") == row.get("href")
        assert row.get("hx-target") == "#app-content"
        assert row.get("hx-push-url") == "true"

    def test_liability_row_shows_the_statement_line_when_present(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        row = one(
            client.get("/accounts").text,
            f'#portfolio a[href="/accounts/{ledger.card}/overview"]',
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
        assert "$100.00" in links[f"/accounts/{ledger.checking}/overview"]
        current = one(switcher, 'a[aria-current="page"]')
        assert current.get("href") == f"/accounts/{ledger.card}/overview"

    def test_manage_menu_follows_the_account_kind(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Rename, the bank and the balance always; rates and collateral
        on a debt; Remove only on a manual account (a provider account
        belongs to its connection).

        Asserted as ACTIONS rather than as their wording: the labels live
        in ``ACCOUNT_ACTION_LABELS`` beside the rule that orders them, so
        this menu and Flet's cannot drift, and renaming an item is a copy
        decision rather than a test failure.
        """
        from app.services.finance.constants import ACCOUNT_ACTION_LABELS as LABELS

        card = client.get(f"/accounts/{ledger.card}").text
        assert [text(li) for li in select(card, "#manage-menu li")] == [
            LABELS[key]
            for key in ("rename", "institution", "reconcile", "terms", "secured_by", "remove")
        ]
        checking = client.get(f"/accounts/{ledger.checking}").text
        assert [text(li) for li in select(checking, "#manage-menu li")] == [
            LABELS[key] for key in ("rename", "institution", "reconcile", "remove")
        ]

    def test_the_header_says_how_current_the_account_is(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """"Updated" means something different per kind of account, and
        the header asks one question of whichever source fills it: a
        connected account is as current as its last sync, an investment
        account as its newest holdings date, a property as its newest
        valuation.

        Staleness is per kind too. Three months is alarming for holdings
        and unremarkable for a house, and one threshold for both would
        teach the reader to ignore the colour.
        """
        from datetime import date

        from app.components.web_frontend.filters import freshness

        today = date(2026, 9, 14)
        # Thresholds are keyed on how often the thing is EXPECTED to
        # change, not on how much we care. Yesterday and last week are
        # both fine for something that syncs daily; a fortnight is not.
        assert freshness(date(2026, 9, 13), "sync", today)["tone"] == "ok"
        assert freshness(date(2026, 9, 7), "sync", today)["tone"] == "ok"
        assert freshness(date(2026, 8, 31), "sync", today)["tone"] == "warn"
        assert freshness(date(2026, 6, 26), "holdings", today)["tone"] == "error"
        # A house valued in June is a normal thing to be holding in
        # September. A colour that cries stale at every ordinary age
        # teaches the reader to ignore it.
        assert freshness(date(2026, 6, 26), "valuation", today)["tone"] == "ok"
        # Nothing at all is unknown, which is not the same as stale.
        assert freshness(None, "sync", today) == {"label": "never", "tone": "muted"}

        page = client.get(f"/accounts/{ledger.card}/overview").text
        one(page, "#account-detail > header [data-updated]")

    def test_all_accounts_offers_no_manage_menu(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        none(client.get("/accounts/all").text, "#manage-menu")

    def test_one_header_on_all_three_faces(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """The tabs are one account. The header says which account, what
        kind, the facts that identify it and what it is worth - the same
        words on the register, the cover sheet and the documents - and
        each Manage item opens its dialog on every one of them.

        It says nothing about what the page below holds: the sub-nav
        carries the document count, and a count in two places is a count
        that will disagree with itself.
        """
        faces = {
            face: one(
                client.get(f"/accounts/{ledger.card}{face}").text,
                "#account-detail > header",
            )
            for face in ("", "/overview", "/documents")
        }
        said = {face: text(one(h, "[data-facts]")) for face, h in faces.items()}
        # One sentence, the same on all three, and only what the badge
        # above has not already said: the kind is up there, USD is the
        # default, and "Manual" was our word for "not connected".
        #
        # "Updated" for a card means when money last moved - not when the
        # row was last touched, which a rename would do.
        # "Not connected", then how current it is with the dot LEADING
        # its words and set apart from the identity - a middot followed
        # by a dot is two dots in a row, and reads as a bullet that lost
        # its text.
        assert set(said.values()) == {"Not connected Updated 2 days ago"}
        for face in faces.values():
            one(face, "[data-updated] [data-dot]")
        assert all("document" not in words for words in said.values())
        assert {
            face: [b.get("hx-get") for b in select(h, "#manage-menu button")]
            for face, h in faces.items()
        } == {
            face: [
                f"/accounts/{ledger.card}/{key}"
                for key in (
                    "rename",
                    "institution",
                    "reconcile",
                    "terms",
                    "secured_by",
                    "remove",
                )
            ]
            for face in faces
        }

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
        row = portfolio_row(page, "Checking")
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
        row = portfolio_row(page, "Checking")
        img = row.find(".//img")
        assert img is not None and "/icons?key=" in (img.get("src") or "")



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


class TestADebtSaysWhatItCosts:
    """The register answers "what happened"; none of it answered "what is
    this costing me". The detail row has held the rate, what is owed and
    where the loan started since the beginning, and ONE line of the
    header read two of its fields - a due date and a minimum payment.
    """

    @pytest.mark.asyncio
    async def test_the_terms_are_drawn_above_the_rows(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from datetime import date

        from app.services.finance.domains.writes.terms import (
            LoanTermsPayload,
            loan_terms_execute,
        )

        account = await seed_account(
            finance, name="GreenSky", account_type="loan", classification="liability"
        )
        await loan_terms_execute(
            async_db_session,
            LoanTermsPayload(
                account_id=int(account.id),
                outstanding_balance=987_366,
                interest_rate_bps=799,
                minimum_payment_amount=22_400,
                next_payment_due_date=date(2026, 10, 12),
                origination_date=date(2019, 3, 1),
                loan_term_months=84,
            ),
            1,
        )
        await async_db_session.commit()

        page = hx.get(f"/accounts/{account.id}").text

        shown = {
            text(one(cell, "dt")): text(select(cell, "dd")[0])
            for cell in select(page, "#account-detail dl.grid > div")
        }
        assert shown["Owed"] == "$9,873.66"
        assert shown["Rate"] == "7.99%"
        assert shown["Payment"] == "$224.00"
        assert shown["Since"] == "Mar 2019"

    @pytest.mark.asyncio
    async def test_a_card_quotes_its_purchase_apr(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A card carries rates in ``aprs``, a loan carries one flat
        field, and the page must not know the difference - the alert and
        the page would otherwise quote different rates for one account."""
        from app.services.finance.models.accounts import FinanceLiabilityDetail

        account = await seed_account(
            finance, name="CITI", account_type="credit_card", classification="liability"
        )
        async_db_session.add(
            FinanceLiabilityDetail(
                owner_user_id=1,
                account_id=int(account.id),
                outstanding_balance=123_456,
                minimum_payment_amount=3_500,
                aprs=[
                    {"apr_type": "cash_apr", "apr_percentage_bps": 3299},
                    {"apr_type": "purchase_apr", "apr_percentage_bps": 2499},
                ],
            )
        )
        await async_db_session.commit()

        page = hx.get(f"/accounts/{account.id}").text

        shown = {
            text(one(cell, "dt")): text(select(cell, "dd")[0])
            for cell in select(page, "#account-detail dl.grid > div")
        }
        assert shown["Rate"] == "24.99%"

    @pytest.mark.asyncio
    async def test_an_account_with_no_terms_draws_no_strip(
        self, hx: TestClient, finance: FinanceService
    ) -> None:
        """One cell is a sentence, not a strip."""
        account = await seed_account(finance, name="Checking")

        page = hx.get(f"/accounts/{account.id}").text

        assert select(page, "#account-detail dl.grid") == []


class TestTheCoverSheet:
    """An account's two faces. The register answers "what happened
    here"; the facts about the account itself would scroll away above a
    thousand rows, so they get their own page under one sub-nav."""

    @pytest.mark.asyncio
    async def test_a_loan_shows_its_terms_and_how_it_pays_down(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.terms import (
            LoanTermsPayload,
            loan_terms_execute,
        )

        account = await seed_account(
            finance, name="GreenSky", account_type="loan", classification="liability"
        )
        await loan_terms_execute(
            async_db_session,
            LoanTermsPayload(
                account_id=int(account.id),
                outstanding_balance=987_366,
                interest_rate_bps=799,
                prepayment_penalty="none",
                extra_payment_treatment="principal",
            ),
            1,
        )
        await async_db_session.commit()

        page = hx.get(f"/accounts/{account.id}/overview").text

        said = [text(el) for el in select(page, "[data-payoff]")]
        assert "no penalty for paying it off early" in said
        assert "reduce the principal immediately" in said

    @pytest.mark.asyncio
    async def test_unconfirmed_reads_as_unconfirmed(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A loan nobody has asked about must not read as a loan with no
        penalty - that is the reading a payoff plan gets built on."""
        from app.services.finance.domains.writes.terms import (
            LoanTermsPayload,
            loan_terms_execute,
        )

        account = await seed_account(
            finance, name="AMEX", account_type="credit_card", classification="liability"
        )
        await loan_terms_execute(
            async_db_session,
            LoanTermsPayload(account_id=int(account.id), outstanding_balance=3_195_605),
            1,
        )
        await async_db_session.commit()

        page = hx.get(f"/accounts/{account.id}/overview").text

        said = [text(el) for el in select(page, "[data-payoff]")]
        assert said == ["not confirmed with the lender"] * 2

    @pytest.mark.asyncio
    async def test_an_asset_is_not_asked_how_it_pays_down(
        self, hx: TestClient, finance: FinanceService
    ) -> None:
        account = await seed_account(finance, name="Checking")

        page = hx.get(f"/accounts/{account.id}/overview").text

        assert select(page, "[data-payoff]") == []

    @pytest.mark.asyncio
    async def test_the_three_faces_share_one_sub_nav(
        self, hx: TestClient, finance: FinanceService
    ) -> None:
        account = await seed_account(finance, name="Checking")

        for path in (
            f"/accounts/{account.id}",
            f"/accounts/{account.id}/overview",
            f"/accounts/{account.id}/documents",
        ):
            tabs = [text(t) for t in select(hx.get(path).text, "#account-tabs a")]
            assert tabs == ["Overview", "Documents", "Register"]

    @pytest.mark.asyncio
    async def test_an_account_with_no_paper_says_so(
        self, hx: TestClient, finance: FinanceService
    ) -> None:
        """On its own tab, which says how much paper there is before you
        click it."""
        account = await seed_account(finance, name="Checking")

        page = hx.get(f"/accounts/{account.id}/documents").text

        assert "Nothing filed here yet" in text(one(page, "[data-empty]"))
        # No count on a tab with nothing behind it.
        assert "Documents (" not in page

    @pytest.mark.asyncio
    async def test_a_document_filed_against_the_account_is_listed(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.service import DocumentService
        from app.services.finance.constants import account_tag

        account = await seed_account(
            finance, name="GreenSky", account_type="loan", classification="liability"
        )
        documents = DocumentService(async_db_session)
        document = await documents.ingest(
            b"%PDF-1.4 statement", title="Aug-2026.pdf", kind="statement"
        )
        await documents.tag(int(document.id), account_tag(int(account.id)))
        await async_db_session.commit()

        page = hx.get(f"/accounts/{account.id}/documents").text

        assert "Aug-2026.pdf" in page
        assert select(page, "[data-empty]") == []
        # And the tab says there is one before you open it.
        assert "Documents (1)" in page

    @pytest.mark.asyncio
    async def test_a_missing_account_is_a_404(
        self, hx: TestClient, finance: FinanceService
    ) -> None:
        assert hx.get("/accounts/99999/overview").status_code == 404


class TestViewingAFiledDocument:
    """What extraction produced is a reading OF the document, not the
    document. A reader opening one is usually checking a figure, and a
    figure has to be checked against the page as it was printed - so the
    original leads and the text that was read sits under it, where the
    two can be seen to disagree."""

    async def _filed(
        self, finance: FinanceService, session: AsyncSession, name: str = "Sched.pdf"
    ) -> tuple[int, int]:
        from app.services.documents.service import DocumentService
        from app.services.finance.constants import account_tag

        account = await seed_account(
            finance, name="Citizens", account_type="loan", classification="liability"
        )
        documents = DocumentService(session)
        document = await documents.ingest(
            b"%PDF-1.4 schedule", title=name, kind="statement", media_type="application/pdf"
        )
        await documents.tag(int(document.id), account_tag(int(account.id)))
        await session.commit()
        return int(account.id), int(document.id)

    @pytest.mark.asyncio
    async def test_the_title_opens_it_in_the_one_modal(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account_id, document_id = await self._filed(finance, async_db_session)

        page = hx.get(f"/accounts/{account_id}/documents").text

        opener = one(page, "[data-open]")
        assert text(one(opener, "span:not([data-file])")) == "Sched.pdf"
        # It has to READ as a door: drawn plain it is indistinguishable
        # from the cells beside it, and the only way to discover it is to
        # click text that looks like text - which is how a working
        # control gets reported as broken.
        assert "text-aegis-teal" in (opener.get("class") or "")
        # And it wears the mark every file manager shows.
        badge = one(opener, "[data-file]")
        assert badge.get("title") == "PDF"
        assert one(badge, "img").get("src") == "/static/icons/files/pdf.svg"
        assert opener.get("hx-target") == "#dialog-body"
        assert opener.get("hx-get") == (
            f"/accounts/{account_id}/documents/{document_id}"
        )

    @pytest.mark.asyncio
    async def test_the_original_leads_not_the_extraction(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account_id, document_id = await self._filed(finance, async_db_session)

        body = hx.get(f"/accounts/{account_id}/documents/{document_id}").text

        original = one(body, "[data-original]")
        assert original.get("data") == f"/api/v1/documents/{document_id}/content"
        # And a way out of the dialog to the file itself.
        assert one(body, "[data-download]").get("href") == (
            f"/api/v1/documents/{document_id}/content"
        )

    @pytest.mark.asyncio
    async def test_a_document_filed_elsewhere_is_not_reachable_here(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The URL names an account; a document that is not filed against
        it does not become readable by guessing the number."""
        account_id, document_id = await self._filed(finance, async_db_session)
        other = await seed_account(finance, name="Checking")

        answer = hx.get(f"/accounts/{other.id}/documents/{document_id}")

        assert answer.status_code == 404


class TestEditingWhatWeSayAboutADocument:
    """The bytes never change - a document is what arrived, and
    correcting it would make the record a lie. What can be corrected is
    everything we SAID about it: the title a lender's filename gave it,
    the kind it was guessed as, the period it covers."""

    async def _filed(
        self, finance: FinanceService, session: AsyncSession
    ) -> tuple[int, int]:
        from app.services.documents.service import DocumentService
        from app.services.finance.constants import account_tag

        account = await seed_account(
            finance, name="Citizens", account_type="loan", classification="liability"
        )
        documents = DocumentService(session)
        document = await documents.ingest(
            b"%PDF-1.4 sched",
            title="Amortization_Schedule.pdf",
            media_type="application/pdf",
        )
        await documents.tag(int(document.id), account_tag(int(account.id)))
        await session.commit()
        return int(account.id), int(document.id)

    @pytest.mark.asyncio
    async def test_the_document_and_its_details_open_together(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """One click. They are read together - checking a figure against
        the page and correcting what it was filed as is one job."""
        account_id, document_id = await self._filed(finance, async_db_session)

        body = hx.get(f"/accounts/{account_id}/documents/{document_id}").text

        one(body, "[data-original]")
        # A lender's schedule is a seven-column table; the dialog grows
        # around what it is showing rather than the opener having to
        # remember to ask.
        one(body, "[data-wide]")
        form = one(body, "form[data-details]")
        assert form.get("hx-post") == (
            f"/accounts/{account_id}/documents/{document_id}"
        )

    @pytest.mark.asyncio
    async def test_saving_changes_the_metadata_and_not_the_file(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.service import DocumentService

        account_id, document_id = await self._filed(finance, async_db_session)
        before = await DocumentService(async_db_session).get(document_id)
        key = before.storage_key

        answer = hx.post(
            f"/accounts/{account_id}/documents/{document_id}",
            data={
                "title": "Citizens amortization schedule",
                "kind": "statement",
                "document_date": "2026-09-13",
                "note": "Lender-generated, authoritative",
            },
        )

        assert answer.status_code == 200
        async_db_session.expire_all()
        after = await DocumentService(async_db_session).get(document_id)
        assert after.title == "Citizens amortization schedule"
        assert after.kind == "statement"
        assert after.note == "Lender-generated, authoritative"
        # The document is what arrived.
        assert after.storage_key == key

    @pytest.mark.asyncio
    async def test_a_title_is_required(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account_id, document_id = await self._filed(finance, async_db_session)

        answer = hx.post(
            f"/accounts/{account_id}/documents/{document_id}",
            data={"title": "  ", "kind": "statement"},
        )

        assert answer.status_code == 422
        assert "Give the document a title." in answer.text

    @pytest.mark.asyncio
    async def test_a_document_filed_elsewhere_cannot_be_edited_here(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account_id, document_id = await self._filed(finance, async_db_session)
        other = await seed_account(finance, name="Checking")

        assert hx.get(
            f"/accounts/{other.id}/documents/{document_id}"
        ).status_code == 404


class TestOneWayToDrawASelect:
    def test_pairs_and_bare_strings_both_become_options(self) -> None:
        """Every form needing a select from a constant had been
        hand-rolling its own; the macro takes {id, name}."""
        from app.components.web_frontend.filters import as_options

        assert as_options([("loan", "Loan")]) == [{"id": "loan", "name": "Loan"}]
        assert as_options(["other_asset"]) == [
            {"id": "other_asset", "name": "Other asset"}
        ]


class TestAFileSaysWhatItIs:
    """A stroke glyph says "a file". Somebody scanning a list of paper is
    looking for "the PDF", which is what every file manager they have
    ever used shows them, in the colour they already know."""

    def test_the_media_type_decides(self) -> None:
        from app.components.web_frontend.glyphs import file_badge

        badge = file_badge("application/pdf")

        assert badge["label"] == "PDF"
        assert badge["icon"].endswith("/pdf.svg")

    def test_the_extension_is_the_fallback(self) -> None:
        """Anything uploaded without a media type still has a name."""
        from app.components.web_frontend.glyphs import file_badge

        assert file_badge(None, "Budget.xlsx")["label"] == "XLS"
        assert file_badge(None, "Letter.docx")["label"] == "DOC"

    def test_an_unknown_format_is_not_guessed(self) -> None:
        """A wrong mark is worse than a neutral one: it is read as a fact
        about the file."""
        from app.components.web_frontend.glyphs import file_badge

        mark = file_badge("application/x-whatever", "mystery")
        assert mark["label"] == "FILE"
        assert mark["icon"].endswith("/file.svg")

    def test_every_mark_is_vendored(self) -> None:
        """An icon that needs the network is an icon that is missing on
        the one screen somebody is offline for."""
        from pathlib import Path

        from app.components.web_frontend.glyphs import _FILE_BADGES, ICON_ROOT

        root = Path("app/components/web_frontend/static") / ICON_ROOT.split("/", 2)[2]
        for _, icon in [*_FILE_BADGES.values(), ("FILE", "file")]:
            assert (root / f"{icon}.svg").exists(), icon


class TestTheValueLine:
    """A column of numbers does not show that a house fell to $285,000
    and recovered past $700,000. The line does, and the moments that
    changed something are marked on it."""

    def _history(self) -> list[dict[str, object]]:
        from datetime import date

        return [
            {"as_of_date": date(2026, 8, 1), "note": "Zestimate",
             "source": "zillow", "value": 71_120_000},
            {"as_of_date": date(2015, 11, 18), "note": "Sold",
             "source": "zillow", "value": 28_500_000},
            {"as_of_date": date(2007, 1, 29), "note": "Prior sale",
             "source": "zillow", "value": 45_100_000},
        ]

    def test_the_line_reads_forwards(self) -> None:
        from app.components.web_frontend.routes.finance.valuations import (
            valuation_chart,
        )

        chart = valuation_chart(self._history())

        assert chart["labels"] == ["Jan 2007", "Nov 2015", "Aug 2026"]
        # Dollars, like every other chart: cents drew a $711,200 house
        # at seventy million.
        assert chart["series"][0]["values"] == [451_000, 285_000, 711_200]

    def _owner(self, paid: int | None) -> object:
        class Property:
            purchase_price = paid

        class Account:
            property = Property() if paid else None

        return Account()

    def test_the_mark_is_where_this_owner_bought_it(self) -> None:
        """A price history describes the PROPERTY and says "Sold" about
        every owner it ever had. The first of those was a stranger's
        purchase in 2007, and that is where a note-matching dot landed -
        labelled "Bought", on somebody else's sale."""
        from app.components.web_frontend.routes.finance.valuations import (
            valuation_chart,
        )

        events = valuation_chart(self._history(), self._owner(28_500_000))["events"]

        assert len(events) == 1
        assert events[0]["at"] == 1
        assert events[0]["label"] == "Bought · $285,000.00"

    def test_the_price_decides_rather_than_the_recorded_date(self) -> None:
        """The price is the figure people keep accurately; the date
        drifts. On the ledger this was written for, the price was exact
        and the date was nine months out."""
        from datetime import date

        from app.components.web_frontend.routes.finance.valuations import (
            purchase_mark,
        )

        series = [
            {"as_of_date": date(2015, 11, 18), "value": 28_500_000},
            {"as_of_date": date(2016, 8, 1), "value": 31_000_000},
        ]

        assert purchase_mark(series, self._owner(28_500_000))[0]["at"] == 0

    def test_a_property_with_no_recorded_price_is_not_guessed_at(self) -> None:
        """A gifted or inherited property legitimately has no purchase
        price, and a dot on the wrong sale is worse than no dot."""
        from app.components.web_frontend.routes.finance.valuations import (
            valuation_chart,
        )

        assert valuation_chart(self._history(), self._owner(None))["events"] == []

    def test_one_point_is_not_a_line(self) -> None:
        from datetime import date

        from app.components.web_frontend.routes.finance.valuations import (
            valuation_chart,
        )

        assert valuation_chart(
            [{"as_of_date": date(2026, 8, 1), "note": "-", "source": "manual",
              "value": 1}]
        ) is None

    @pytest.mark.asyncio
    async def test_the_page_draws_it(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from datetime import date

        from app.services.finance.domains.writes.terms import (
            ValuationPayload,
            valuation_execute,
        )

        house = await seed_account(
            finance, name="House Bedner", account_type="property",
            classification="asset",
        )
        await valuation_execute(
            async_db_session,
            ValuationPayload(
                account_id=int(house.id),
                source="zillow",
                points=[
                    {"as_of_date": date(2015, 11, 18), "value": 28_500_000,
                     "note": "Sold"},
                    {"as_of_date": date(2026, 8, 1), "value": 71_120_000,
                     "note": "Zestimate", "is_estimate": True},
                ],
            ),
            1,
        )
        await async_db_session.commit()

        page = hx.get(f"/accounts/{house.id}/overview").text

        one(page, "canvas[data-chart=line]")
        data = json.loads(text(one(page, "#chart-valuations-data")))
        assert data["series"][0]["values"] == [285_000.0, 711_200.0]
        # No purchase price on this account, so nothing is marked.
        assert data["events"] == []


class TestTheValueWindow:
    """The same chips the register and the bills page wear. A ledger is
    read in days and a house is held in decades, so the scale differs -
    but a second way to say "how far back" is a second thing to keep in
    step, so it is one macro, one field and one ``since()``."""

    async def _house(
        self, finance: FinanceService, session: AsyncSession
    ) -> int:
        from datetime import date, timedelta

        from app.services.finance.domains.writes.terms import (
            ValuationPayload,
            valuation_execute,
        )
        from app.services.finance.utils import current_date

        house = await seed_account(
            finance, name="House Bedner", account_type="property",
            classification="asset",
        )
        today = current_date()
        await valuation_execute(
            session,
            ValuationPayload(
                account_id=int(house.id),
                source="zillow",
                points=[
                    {"as_of_date": date(2007, 1, 29), "value": 45_100_000,
                     "note": "Prior sale"},
                    {"as_of_date": today - timedelta(days=200),
                     "value": 68_000_000, "note": "Estimate"},
                    {"as_of_date": today - timedelta(days=10),
                     "value": 71_120_000, "note": "Estimate"},
                ],
            ),
            1,
        )
        await session.commit()
        return int(house.id)

    @pytest.mark.asyncio
    async def test_the_chips_are_the_one_macro(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        house = await self._house(finance, async_db_session)

        page = hx.get(f"/accounts/{house}/overview").text

        chips = select(page, "#value-window input[name=days]")
        assert [c.get("value") for c in chips] == ["365", "1825", "3650", "9999"]

    @pytest.mark.asyncio
    async def test_the_window_narrows_the_line_and_the_rows_together(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A table that disagrees with the chart above it is worse than
        either on its own."""
        house = await self._house(finance, async_db_session)

        page = hx.get(f"/accounts/{house}/overview?days=365").text

        chart = json.loads(text(one(page, "#chart-valuations-data")))
        assert len(chart["labels"]) == 2
        assert len(table_rows(page, "#card-value-history")) == 2

    @pytest.mark.asyncio
    async def test_all_keeps_the_whole_history(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        house = await self._house(finance, async_db_session)

        page = hx.get(f"/accounts/{house}/overview?days=9999").text

        assert len(json.loads(text(one(page, "#chart-valuations-data")))["labels"]) == 3


class TestTheRunUpToBuyingIt:
    """Most windows are a number of days back from today. Two are not:
    what a house had been doing BEFORE somebody walked in, and what it
    has done since, are positions in that house's own story - and a
    house is the thing people ask about that way."""

    async def _house(
        self, finance: FinanceService, session: AsyncSession
    ) -> int:
        from datetime import date

        from app.services.finance.domains.ledger.properties import (
            set_property_metadata,
        )
        from app.services.finance.domains.writes.terms import (
            ValuationPayload,
            valuation_execute,
        )

        house = await seed_account(
            finance, name="House Bedner", account_type="property",
            classification="asset",
        )
        house.metadata_ = set_property_metadata(
            house.metadata_,
            purchase_price=28_500_000,
            purchase_date=date(2015, 11, 18),
        )
        session.add(house)
        await session.flush()
        await valuation_execute(
            session,
            ValuationPayload(
                account_id=int(house.id),
                source="zillow",
                points=[
                    {"as_of_date": date(2007, 1, 29), "value": 45_100_000,
                     "note": "Prior sale"},
                    {"as_of_date": date(2014, 10, 13), "value": 32_250_000,
                     "note": "Listed for sale"},
                    {"as_of_date": date(2015, 11, 18), "value": 28_500_000,
                     "note": "Sold"},
                    {"as_of_date": date(2026, 8, 1), "value": 71_120_000,
                     "note": "Estimate"},
                ],
            ),
            1,
        )
        await session.commit()
        return int(house.id)

    @pytest.mark.asyncio
    async def test_before_stops_at_the_purchase(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        house = await self._house(finance, async_db_session)

        page = hx.get(f"/accounts/{house}/overview?days=-1").text

        chart = json.loads(text(one(page, "#chart-valuations-data")))
        assert chart["labels"] == ["Jan 2007", "Oct 2014", "Nov 2015"]

    @pytest.mark.asyncio
    async def test_since_starts_at_the_purchase(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The purchase belongs to both halves: the end of the run-up
        and the start of what you have done with it."""
        house = await self._house(finance, async_db_session)

        page = hx.get(f"/accounts/{house}/overview?days=-2").text

        chart = json.loads(text(one(page, "#chart-valuations-data")))
        assert chart["labels"] == ["Nov 2015", "Aug 2026"]

    @pytest.mark.asyncio
    async def test_the_chips_appear_only_where_a_purchase_is_known(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A gifted or inherited property has no purchase to measure
        from, and a chip that cannot answer is worse than no chip."""
        house = await self._house(finance, async_db_session)
        plain = await seed_account(
            finance, name="Dad's House", account_type="property",
            classification="asset",
        )

        with_purchase = hx.get(f"/accounts/{house}/overview").text
        without = hx.get(f"/accounts/{plain.id}/overview").text

        assert "Before I bought" in with_purchase
        assert "Before I bought" not in without


class TestALongHistoryScrollsInsideItsCard:
    """A decade of monthly valuations is a page of numbers where the
    first rows are the answer and the rest is there to be checked."""

    @pytest.mark.asyncio
    async def test_the_value_history_is_capped_and_scrolls(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from datetime import date

        from app.services.finance.domains.writes.terms import (
            ValuationPayload,
            valuation_execute,
        )

        house = await seed_account(
            finance, name="House Bedner", account_type="property",
            classification="asset",
        )
        await valuation_execute(
            async_db_session,
            ValuationPayload(
                account_id=int(house.id),
                source="zillow",
                points=[
                    {"as_of_date": date(2020, month, 1), "value": 50_000_000 + month}
                    for month in range(1, 13)
                ],
            ),
            1,
        )
        await async_db_session.commit()

        page = hx.get(f"/accounts/{house.id}/overview").text

        wrapper = one(page, "#card-value-history div.overflow-x-auto")
        assert "max-h-80" in (wrapper.get("class") or "")
        assert "overflow-y-auto" in (wrapper.get("class") or "")
        # The labels have to survive the scroll, or it is a column of
        # numbers with no column headings.
        assert "sticky" in (one(page, "#card-value-history thead").get("class") or "")


class TestWhatIsLeftAfterTheMortgage:
    """The question a property is actually held against: what is it
    worth, what is owed on it, what is left, and how much of the value
    the debt is. Every figure derives from the CONFIRMED lien links at
    read time - nothing is stored, so nothing can disagree with the
    accounts it came from."""

    async def _house_and_mortgage(
        self, finance: FinanceService, session: AsyncSession, *, linked: bool = True
    ) -> int:
        from app.services.finance.domains.ledger.properties import (
            set_secured_debt,
        )
        from app.services.finance.domains.ledger.valuations import upsert_valuation
        from app.services.finance.utils import current_date

        house = await seed_account(
            finance, name="House Bedner", account_type="property",
            classification="asset",
        )
        mortgage = await seed_account(
            finance, name="Citizens Bank Mortgage", account_type="loan",
            classification="liability",
        )
        await upsert_valuation(
            session,
            account_id=int(house.id),
            as_of_date=current_date(),
            value=71_120_000,
            owner_user_id=1,
            source="zillow",
        )
        mortgage.current_balance = -17_595_804
        session.add(mortgage)
        if linked:
            await set_secured_debt(
                session,
                int(mortgage.id),
                owner_user_id=1,
                secured_by_account_id=int(house.id),
                lien_position=1,
            )
        await session.commit()
        return int(house.id)

    @pytest.mark.asyncio
    async def test_the_four_figures(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        house = await self._house_and_mortgage(finance, async_db_session)

        page = hx.get(f"/accounts/{house}/overview").text

        shown = {
            text(select(cell, "dt span")[0]): text(select(cell, "dd")[0])
            for cell in select(page, "#account-detail dl.grid > div")
        }
        assert shown["Value"] == "$711,200.00"
        assert shown["Secured"] == "$175,958.04"
        assert shown["Equity"] == "$535,241.96"
        assert shown["LTV"] == "24.74%"

    @pytest.mark.asyncio
    async def test_the_debt_that_secures_it_is_named(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """"Secured" is a number until it says by what."""
        house = await self._house_and_mortgage(finance, async_db_session)

        page = hx.get(f"/accounts/{house}/overview").text

        assert "Citizens Bank Mortgage" in page

    @pytest.mark.asyncio
    async def test_an_unlinked_property_claims_nothing(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A property with no confirmed lien showing 100% equity is a
        claim nobody made."""
        house = await self._house_and_mortgage(
            finance, async_db_session, linked=False
        )

        page = hx.get(f"/accounts/{house}/overview").text

        labels = [
            text(select(cell, "dt span")[0])
            for cell in select(page, "#account-detail dl.grid > div")
        ]
        assert "LTV" not in labels

    @pytest.mark.asyncio
    async def test_every_figure_can_explain_itself(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """Four figures nobody is born knowing. Each one opens its own
        words in the one modal, and says so with a mark - the hover fill
        is only found by a mouse already on it."""
        house = await self._house_and_mortgage(finance, async_db_session)

        page = hx.get(f"/accounts/{house}/overview").text

        cells = select(page, "#account-detail dl.grid > div[role=button]")
        assert [text(select(c, "dt span")[0]) for c in cells] == [
            "Value",
            "Secured",
            "Equity",
            "LTV",
        ]
        assert all(
            c.get("hx-get") == f"/accounts/{house}/figures/{key}"
            for c, key in zip(cells, ["value", "secured", "equity", "ltv"])
        )
        assert all(select(c, "dt span[aria-hidden]") for c in cells)

    @pytest.mark.asyncio
    async def test_the_explanation_does_the_arithmetic(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A textbook definition of equity is worth less than this
        house's: the dialog shows the subtraction that made the number
        on the page behind it."""
        house = await self._house_and_mortgage(finance, async_db_session)

        body = hx.get(f"/accounts/{house}/figures/equity").text

        assert [text(row) for row in select(body, "dl > div")] == [
            "Value $711,200.00",
            "Less secured -$175,958.04",
            "Equity $535,241.96",
        ]

    @pytest.mark.asyncio
    async def test_a_figure_the_property_does_not_have(
        self, hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """Which figures exist depends on what is linked, so a key with
        no cell is a 404 rather than an empty modal."""
        house = await self._house_and_mortgage(
            finance, async_db_session, linked=False
        )

        assert hx.get(f"/accounts/{house}/figures/ltv").status_code == 404
