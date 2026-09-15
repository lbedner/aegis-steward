"""Whose money an account holds.

The app tracks a parent's pension because somebody has to, and the day
it does, every household total has to keep being a statement about the
household. So the listing defaults to ours and a caller that means
everybody says so.
"""

from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.service import PartyService
from tests.web.dom import none, one, select, text


@pytest.fixture
def organization(async_db_session: AsyncSession) -> object:
    """An organization, written into the session the finance pages read
    through - see ``person`` for why it is not made over HTTP."""

    async def make(name: str, website: str = "") -> str:
        party = await PartyService(async_db_session).create(
            name=name,
            kind="organization",
            contact={"website": f"https://{website}"} if website else {},
        )
        await async_db_session.flush()
        return str(party.id)

    return make


@pytest.fixture
def person(async_db_session: AsyncSession) -> object:
    """A party, written into the session the pages read through.

    Not through the People route: the finance pages hold the request's
    session and the settings pages open their own, so in a test the two
    see different worlds. In the app they are one database and one
    session per request.
    """

    async def make(name: str) -> str:
        party = await PartyService(async_db_session).create(name=name, kind="person")
        await async_db_session.flush()
        return str(party.id)

    return make


def _account(client: TestClient, name: str, balance: str, whose: str = "") -> None:
    client.post(
        "/accounts/new",
        data={
            "name": name,
            "account_type": "checking",
            "opening_balance": balance,
            "whose": whose,
        },
    )


class TestSomebodyElsesMoney:
    @pytest.mark.asyncio
    async def test_their_account_stays_out_of_our_net_worth(
        self, client: TestClient, person: Any
    ) -> None:
        """The number at the top of the portfolio is ours. A pension
        tracked for a parent in care is not a thing we own."""
        subject = await person("Whose Subject One")
        before = text(one(client.get("/accounts").text, "[data-net-worth]"))

        _account(client, "Whose Their Pension", "4000.00", whose=subject)

        after = text(one(client.get("/accounts").text, "[data-net-worth]"))
        assert after == before
        assert "Whose Their Pension" not in text(
            one(client.get("/accounts").text, "#portfolio")
        )

    def test_our_own_account_still_counts(self, client: TestClient) -> None:
        before = text(one(client.get("/accounts").text, "[data-net-worth]"))

        _account(client, "Whose Our Checking", "150.00")

        page = client.get("/accounts").text
        assert "Whose Our Checking" in text(one(page, "#portfolio"))
        assert text(one(page, "[data-net-worth]")) != before

    @pytest.mark.asyncio
    async def test_the_page_says_what_it_is_holding_and_not_counting(
        self, client: TestClient, person: Any
    ) -> None:
        """Money held and not counted is invisible by construction.
        Approving an account for a parent and finding the portfolio
        unchanged reads as nothing having happened."""
        subject = await person("Whose Subject Four")
        _account(client, "Whose Their Pension Four", "1200.00", whose=subject)

        page = client.get("/accounts").text
        line = one(page, "[data-also-held]")

        assert "Whose Subject Four" in text(line)
        assert "Whose Their Pension Four" in text(line)
        assert "not counted here" in text(line)
        assert one(line, "a").get("href").endswith(f"?whose={subject}")

    @pytest.mark.asyncio
    async def test_their_own_page_does_not_repeat_the_line(
        self, client: TestClient, person: Any
    ) -> None:
        """On their page the whole list is theirs; saying it again is
        the app talking to itself."""
        subject = await person("Whose Subject Five")
        _account(client, "Whose Their Pension Five", "1200.00", whose=subject)

        none(client.get(f"/accounts?whose={subject}").text, "[data-also-held]")

    @pytest.mark.asyncio
    async def test_their_money_is_a_click_away_never_a_default(
        self, client: TestClient, person: Any
    ) -> None:
        subject = await person("Whose Subject Two")
        _account(client, "Whose Their Annuity", "2500.00", whose=subject)

        page = client.get("/accounts").text
        chips = {text(el) for el in select(page, "[data-whose] a")}
        assert {"Ours", "Whose Subject Two", "Everyone"} <= chips

        theirs = client.get(f"/accounts?whose={subject}").text
        assert "Whose Their Annuity" in text(one(theirs, "#portfolio"))

    @pytest.mark.asyncio
    async def test_a_typo_in_the_filter_does_not_widen_the_total(
        self, client: TestClient, person: Any
    ) -> None:
        """A URL nobody typed carefully must not quietly fold somebody
        else's money into ours."""
        subject = await person("Whose Subject Three")
        _account(client, "Whose Their Trust", "9000.00", whose=subject)

        page = client.get("/accounts?whose=nonsense").text

        assert "Whose Their Trust" not in text(one(page, "#portfolio"))

    def test_the_filter_is_absent_when_it_has_one_answer(
        self, client: TestClient
    ) -> None:
        """A household tracking nobody else's money never sees a control
        whose only option is where it already is."""
        none(client.get("/accounts").text, "[data-whose]")

    @pytest.mark.asyncio
    async def test_their_account_opens_instead_of_404ing(
        self, client: TestClient, person: Any
    ) -> None:
        """The page had told the reader this account exists and then
        refused to show it, because a door is not a total: the listing
        behind it was scoped to our money."""
        subject = await person("Whose Subject Six")
        _account(client, "Whose Their Pension Six", "1200.00", whose=subject)
        theirs = client.get(f"/accounts?whose={subject}").text
        door = [
            el.get("href")
            for el in select(theirs, "#portfolio a")
            if "Pension Six" in text(el)
        ]

        page = client.get(door[0])

        assert page.status_code == 200
        assert "Whose Their Pension Six" in text(one(page.text, "#app-content"))

    @pytest.mark.asyncio
    async def test_every_tab_of_their_account_opens(
        self, client: TestClient, person: Any
    ) -> None:
        """Overview, Documents and Register are three doors to one
        account. Fixing one and leaving the others is how a page ends up
        with a header naming an account its own tab cannot open."""
        subject = await person("Whose Subject Eight")
        _account(client, "Whose Their Pension Eight", "1200.00", whose=subject)
        theirs = client.get(f"/accounts?whose={subject}").text
        base = [
            el.get("href")
            for el in select(theirs, "#portfolio a")
            if "Pension Eight" in text(el)
        ][0].removesuffix("/overview")

        for tab in (f"{base}/overview", f"{base}/documents", base):
            assert client.get(tab).status_code == 200, tab

    @pytest.mark.asyncio
    async def test_their_page_totals_their_money_not_ours(
        self, client: TestClient, person: Any
    ) -> None:
        """A header that summed our accounts over their pension would be
        two people's money in one figure."""
        subject = await person("Whose Subject Seven")
        _account(client, "Whose Our Checking Seven", "500.00")
        _account(client, "Whose Their Pension Seven", "1200.00", whose=subject)
        theirs = client.get(f"/accounts?whose={subject}").text

        assert "Whose Our Checking Seven" not in text(one(theirs, "#portfolio"))
        assert "Whose Their Pension Seven" in text(one(theirs, "#portfolio"))


class TestWhatAnAssetIsWorth:
    def test_any_asset_can_say_what_it_is_worth(self, client: TestClient) -> None:
        """The page draws a value history for any asset and only a
        property was offered the door to record one - so a pension's
        reserve, an annuity or a vehicle had a chart nothing could fill."""
        client.post(
            "/accounts/new",
            data={
                "name": "Worth Their Pension",
                "account_type": "other_asset",
                "opening_balance": "",
            },
        )
        listing = client.get("/accounts").text
        base = [
            el.get("href")
            for el in select(listing, "#portfolio a")
            if "Worth Their Pension" in text(el)
        ][0].removesuffix("/overview")

        page = client.get(f"{base}/overview").text
        offered = [el.get("hx-get") for el in select(page, "[role=menu] button")]

        assert f"{base}/valuations" in offered
        assert client.get(f"{base}/valuations").status_code == 200

    def test_a_debt_is_not_asked_what_it_is_worth(self, client: TestClient) -> None:
        """A debt records what is OWED; what it is worth is a question
        for an asset."""
        client.post(
            "/accounts/new",
            data={
                "name": "Worth Their Card",
                "account_type": "credit_card",
                "opening_balance": "100.00",
            },
        )
        listing = client.get("/accounts").text
        base = [
            el.get("href")
            for el in select(listing, "#portfolio a")
            if "Worth Their Card" in text(el)
        ][0].removesuffix("/overview")

        page = client.get(f"{base}/overview").text
        offered = [el.get("hx-get") for el in select(page, "[role=menu] button")]

        assert f"{base}/valuations" not in offered


class TestHeldWith:
    @pytest.mark.asyncio
    async def test_an_organization_is_offered_rather_than_typed_again(
        self, client: TestClient, organization: Any
    ) -> None:
        """A pension fund is already a party. Typing it here would make a
        second row of one body, with a website on one and a logo on the
        other."""
        await organization("Held Pension Fund", "heldpension.example.com")
        client.post(
            "/accounts/new",
            data={
                "name": "Held Their Pension",
                "account_type": "other_asset",
                "opening_balance": "",
            },
        )
        listing = client.get("/accounts").text
        base = [
            el.get("href")
            for el in select(listing, "#portfolio a")
            if "Held Their Pension" in text(el)
        ][0].removesuffix("/overview")

        chooser = client.get(f"{base}/institution").text
        offered = {
            el.get("value"): text(el)
            for el in select(chooser, "button[name=institution_id]")
        }
        party_option = next(
            value for value, label in offered.items() if label == "Held Pension Fund"
        )
        assert party_option.startswith("party:")

        client.post(f"{base}/institution", data={"institution_id": party_option})

        page = client.get(f"{base}/overview").text
        held = one(page, "[data-held-with]")
        assert text(held) == "Held Pension Fund"
        # The party's website came with it: the ledger row is made from
        # the address book rather than beside it.
        assert held.get("href") == "https://heldpension.example.com"


class TestTheAccountFrontPage:
    """An account with no transactions is not an account with nothing to
    say: whose money it is and who it is held with are rows in the
    address book, with the phone number somebody actually needs."""

    @pytest.mark.asyncio
    async def test_it_names_the_person_and_the_place_with_their_details(
        self, client: TestClient, person: Any, organization: Any
    ) -> None:
        subject = await person("Front Subject One")
        place = await organization("Front Pension Fund", "frontpension.example.com")
        client.post(
            "/accounts/new",
            data={
                "name": "Front Their Pension",
                "account_type": "other_asset",
                "opening_balance": "",
                "whose": subject,
            },
        )
        theirs = client.get(f"/accounts?whose={subject}").text
        base = [
            el.get("href")
            for el in select(theirs, "#portfolio a")
            if "Front Their Pension" in text(el)
        ][0].removesuffix("/overview")
        client.post(f"{base}/institution", data={"institution_id": f"party:{place}"})

        page = client.get(f"{base}/overview").text

        whose = one(page, '[data-who="whose-money"]')
        held = one(page, '[data-who="held-with"]')
        assert text(one(whose, "[data-party]")) == "Front Subject One"
        assert text(one(held, "[data-party]")) == "Front Pension Fund"
        assert one(held, "[data-website]").get("href") == (
            "https://frontpension.example.com"
        )

    def test_an_ordinary_account_gets_no_empty_card(
        self, client: TestClient
    ) -> None:
        """An empty card on every account teaches people to skip the
        space it sits in."""
        client.post(
            "/accounts/new",
            data={
                "name": "Front Our Savings",
                "account_type": "savings",
                "opening_balance": "10.00",
            },
        )
        listing = client.get("/accounts").text
        base = [
            el.get("href")
            for el in select(listing, "#portfolio a")
            if "Front Our Savings" in text(el)
        ][0].removesuffix("/overview")

        none(client.get(f"{base}/overview").text, "[data-who]")
