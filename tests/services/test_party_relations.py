"""Who is related to whom (#289).

"What children do you have under this household" - none recorded, and
after Robin and Casey were added she could only say they shared the
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
    sam = await people.create(name="Sam Doe", kind="person")
    alex = await people.create(name="Alex Doe", kind="person")
    robin = await people.create(name="Robin Doe", kind="person")
    await db.flush()
    return int(sam.id or 0), int(alex.id or 0), int(robin.id or 0)


class TestRelating:
    @pytest.mark.asyncio
    async def test_a_relationship_reads_from_both_sides(
        self, async_db_session: AsyncSession
    ) -> None:
        sam, alex, robin = await _family(async_db_session)

        assert await relations.relate(async_db_session, sam, robin, "parent")
        assert not await relations.relate(async_db_session, sam, robin, "parent")
        await relations.relate(async_db_session, sam, alex, "spouse")

        known = await relations.relations_for(async_db_session, [sam, robin])
        assert sorted((r["relation"], r["party_id"]) for r in known[sam]) == [
            ("parent of", robin),
            ("spouse of", alex),
        ]
        assert [(r["relation"], r["party_id"]) for r in known[robin]] == [
            ("child of", sam)
        ]

    @pytest.mark.asyncio
    async def test_a_relationship_can_be_taken_back(
        self, async_db_session: AsyncSession
    ) -> None:
        sam, _alex, robin = await _family(async_db_session)
        await relations.relate(async_db_session, sam, robin, "parent")

        await relations.unrelate(async_db_session, sam, robin, "parent")

        assert (await relations.relations_for(async_db_session, [sam]))[sam] == []


class TestTheCard:
    @pytest.mark.asyncio
    async def test_she_proposes_it_and_it_lands(
        self, async_db_session: AsyncSession
    ) -> None:
        sam, _alex, robin = await _family(async_db_session)
        payload = RelateContactsPayload(
            party_id=sam, related_party_id=robin, relation="parent"
        )

        said = [
            r.value
            for r in await relate_contacts_describe(async_db_session, payload, None)
        ]
        await relate_contacts_execute(async_db_session, payload, None)

        assert said == ["Sam Doe is parent of Robin Doe"]
        known = await relations.relations_for(async_db_session, [robin])
        assert known[robin][0]["relation"] == "child of"

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
        from app.services.matters import tools as ai_tools

        monkeypatch.setattr(ai_tools, "get_async_session", opens(async_db_session))
        sam, _alex, robin = await _family(async_db_session)
        await relations.relate(async_db_session, sam, robin, "parent")
        await async_db_session.commit()

        found = {p["id"]: p for p in (await ai_tools.parties())["parties"]}

        assert found[sam]["relations"] == [
            {"relation": "parent of", "party_id": robin, "party": "Robin Doe"}
        ]
