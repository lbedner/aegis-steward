"""Who is related to whom (#289).

"What children do you have under this household" - none recorded, and
after Vanessa and Mariah were added she could only say they shared the
address. A relationship between two people is recorded (spouse, parent
of, or other), proposed as a card, and read back from both sides.
"""

from pydantic import ValidationError
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters import relations
from app.services.matters.contacts import (
    RelateContactsPayload,
    relate_contacts_describe,
    relate_contacts_execute,
)
from app.services.matters.service import PartyService
from tests._session import opens

# Each test relates a few people one at a time, on purpose.
pytestmark = pytest.mark.queryspy(threshold=4)


async def _family(db: AsyncSession) -> tuple[int, int, int]:
    people = PartyService(db)
    leonard = await people.create(name="Leonard Bedner", kind="person")
    marisa = await people.create(name="Marisa Bedner", kind="person")
    vanessa = await people.create(name="Vanessa Bedner", kind="person")
    await db.flush()
    return int(leonard.id or 0), int(marisa.id or 0), int(vanessa.id or 0)


class TestRelating:
    @pytest.mark.asyncio
    async def test_a_relationship_reads_from_both_sides(
        self, async_db_session: AsyncSession
    ) -> None:
        leonard, marisa, vanessa = await _family(async_db_session)

        assert await relations.relate(async_db_session, leonard, vanessa, "parent")
        assert not await relations.relate(async_db_session, leonard, vanessa, "parent")
        await relations.relate(async_db_session, leonard, marisa, "spouse")

        known = await relations.relations_for(async_db_session, [leonard, vanessa])
        assert sorted((r["relation"], r["party_id"]) for r in known[leonard]) == [
            ("parent of", vanessa),
            ("spouse of", marisa),
        ]
        assert [(r["relation"], r["party_id"]) for r in known[vanessa]] == [
            ("child of", leonard)
        ]

    @pytest.mark.asyncio
    async def test_a_relationship_can_be_taken_back(
        self, async_db_session: AsyncSession
    ) -> None:
        leonard, _marisa, vanessa = await _family(async_db_session)
        await relations.relate(async_db_session, leonard, vanessa, "parent")

        await relations.unrelate(async_db_session, leonard, vanessa, "parent")

        assert (await relations.relations_for(async_db_session, [leonard]))[
            leonard
        ] == []


class TestTheCard:
    @pytest.mark.asyncio
    async def test_she_proposes_it_and_it_lands(
        self, async_db_session: AsyncSession
    ) -> None:
        leonard, _marisa, vanessa = await _family(async_db_session)
        payload = RelateContactsPayload(
            party_id=leonard, related_party_id=vanessa, relation="parent"
        )

        said = [
            r.value
            for r in await relate_contacts_describe(async_db_session, payload, None)
        ]
        await relate_contacts_execute(async_db_session, payload, None)

        assert said == ["Leonard Bedner is parent of Vanessa Bedner"]
        known = await relations.relations_for(async_db_session, [vanessa])
        assert known[vanessa][0]["relation"] == "child of"

    def test_only_known_relationships_between_two_people(self) -> None:
        with pytest.raises(ValidationError):
            RelateContactsPayload(party_id=1, related_party_id=2, relation="cousin")
        with pytest.raises(ValidationError):
            RelateContactsPayload(party_id=1, related_party_id=1, relation="spouse")


class TestHerRead:
    @pytest.mark.asyncio
    async def test_who_are_my_children_resolves(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.matters import ai_tools

        monkeypatch.setattr(ai_tools, "get_async_session", opens(async_db_session))
        leonard, _marisa, vanessa = await _family(async_db_session)
        await relations.relate(async_db_session, leonard, vanessa, "parent")
        await async_db_session.commit()

        found = {p["id"]: p for p in (await ai_tools.parties())["parties"]}

        assert found[leonard]["relations"] == [
            {"relation": "parent of", "party_id": vanessa, "party": "Vanessa Bedner"}
        ]
