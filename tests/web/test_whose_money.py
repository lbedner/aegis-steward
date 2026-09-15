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
