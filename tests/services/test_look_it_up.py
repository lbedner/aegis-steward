"""Looking a contact up by hand: one button, the assistant's own lookup.

The paper first, then the web: a web search only for what the record
and its paper still lack. What it finds is ONE contact.amend card, each
field citing where it was read, so pressing the button never writes a
thing by itself (#173 follow-up, 2026-09-23).
"""

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tests._session import opens


async def _eleanor(db: AsyncSession, **contact: str) -> Any:
    from app.services.matters.service import PartyService

    party = await PartyService(db).create(
        name="Testcase Eleanor Care", kind="organization", contact=contact or None
    )
    await db.flush()
    return party


def _web(found: list[dict[str, Any]], asked: list[Any] | None = None) -> Any:
    async def web_offers(
        name: str, guesses: list[str], known: set[str]
    ) -> dict[str, Any]:
        if asked is not None:
            asked.append((name, list(guesses), set(known)))
        return {
            "confirmed": "example.org/eleanor/" if found else None,
            "found_by": "search",
            "offers": found,
        }

    return web_offers


PHONE = {
    "field": "phone",
    "value": "(845) 229-9177",
    "url": "https://example.org/eleanor/",
    "source": "found by web search; read at https://example.org/eleanor/",
}


class TestLookingItUp:
    @pytest.mark.asyncio
    async def test_what_the_web_finds_becomes_one_card_that_cites_it(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.matters import domain_lookup
        from app.services.matters.lookup import propose_look_up

        party = await _eleanor(async_db_session)
        monkeypatch.setattr(domain_lookup, "web_offers", _web([PHONE]))

        card = await propose_look_up(opens(async_db_session), party.id)

        assert card is not None and card.change_type == "contact.amend"
        assert card.payload["phone"] == "(845) 229-9177"
        assert card.payload["sources"]["phone"].startswith("found by web search")

    @pytest.mark.asyncio
    async def test_a_field_the_record_has_is_never_offered(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.matters import domain_lookup
        from app.services.matters.lookup import propose_look_up

        party = await _eleanor(async_db_session, phone="845-229-9177")
        monkeypatch.setattr(domain_lookup, "web_offers", _web([PHONE]))

        assert await propose_look_up(opens(async_db_session), party.id) is None

    @pytest.mark.asyncio
    async def test_a_website_on_file_is_what_gets_checked(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No model to guess with, so the site the record already names is
        the guess; with none, it goes straight to the search."""
        from app.services.matters import domain_lookup
        from app.services.matters.lookup import propose_look_up

        asked: list[Any] = []
        party = await _eleanor(async_db_session, website="example.org/eleanor/")
        monkeypatch.setattr(domain_lookup, "web_offers", _web([], asked))

        await propose_look_up(opens(async_db_session), party.id)

        assert asked[0][1] == ["example.org/eleanor/"]

    @pytest.mark.asyncio
    async def test_nothing_found_is_no_card(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.matters import domain_lookup
        from app.services.matters.lookup import propose_look_up

        party = await _eleanor(async_db_session)
        monkeypatch.setattr(domain_lookup, "web_offers", _web([]))

        assert await propose_look_up(opens(async_db_session), party.id) is None

    @pytest.mark.asyncio
    async def test_a_person_is_never_searched_for(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A search names the ORGANIZATION, never the household (#173). A
        person's lookup reads their own paper and nothing else."""
        from app.services.matters import domain_lookup
        from app.services.matters.lookup import propose_look_up
        from app.services.matters.service import PartyService

        asked: list[Any] = []
        person = await PartyService(async_db_session).create(
            name="Testcase James", kind="person"
        )
        await async_db_session.flush()
        monkeypatch.setattr(domain_lookup, "web_offers", _web([PHONE], asked))

        assert await propose_look_up(opens(async_db_session), person.id) is None
        assert asked == []

    @pytest.mark.asyncio
    async def test_pressing_it_again_shows_the_card_already_waiting(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second press stacked a second, identical card. The one
        still waiting is the answer."""
        from app.services.matters import domain_lookup
        from app.services.matters.lookup import propose_look_up

        party = await _eleanor(async_db_session)
        monkeypatch.setattr(domain_lookup, "web_offers", _web([PHONE]))

        first = await propose_look_up(opens(async_db_session), party.id)
        second = await propose_look_up(opens(async_db_session), party.id)

        assert second is not None and second.id == first.id


class TestTheCardReadsCleanly:
    @pytest.mark.asyncio
    async def test_where_a_value_came_from_sits_under_it(
        self, async_db_session: AsyncSession
    ) -> None:
        """Value and source ran together on one line, "(845) 229-9177 ·
        found by web search; read at https://...", which was hard to read
        (2026-09-24). The source is the row's note, drawn beneath it."""
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_describe,
        )

        party = await _eleanor(async_db_session)
        rows = await amend_contact_describe(
            async_db_session,
            AmendContactPayload(
                party_id=party.id,
                phone="(845) 229-9177",
                sources={"phone": PHONE["source"]},
            ),
            None,
        )
        phone = next(row for row in rows if row.label == "Phone")
        assert phone.value == "- → (845) 229-9177"
        assert phone.note == PHONE["source"]
