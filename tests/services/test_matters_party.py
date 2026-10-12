"""The party: one row for everyone anything points at.

ST-01. The validation gate from the ticket is three rows - a person, an
agency and a facility - each carrying no role, because a role belongs to
the relationship rather than to the party.
"""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.models import Party, sort_name_for
from app.services.matters.service import PartyService


class TestAParty:
    @pytest.mark.asyncio
    async def test_the_three_the_case_needs(
        self, async_db_session: AsyncSession
    ) -> None:
        """The Medicaid renewal named a person, an agency and a facility.
        One table holds all three, and none of them carries what it IS to
        the case - that belongs to the matter that names them."""
        parties = PartyService(async_db_session)

        james = await parties.create(name="James Doe", kind="person")
        dss = await parties.create(name="Example County DSS", kind="organization")
        facility = await parties.create(
            name="Maple Nursing Care Center", kind="organization"
        )
        await async_db_session.commit()

        assert [p.id for p in (james, dss, facility)] == sorted(
            p.id for p in (james, dss, facility)
        )
        # A person files under their last name; an organization under its
        # own. The reader looks for "Doe", not "James".
        assert james.sort_name == "Doe, James"
        assert dss.sort_name == "Example County DSS"
        # No role anywhere on the row.
        assert not hasattr(james, "role")
        # Filing order, which is not alphabetical by display name:
        # "James Doe" files under D and therefore leads.
        listed = await parties.find()
        assert [p.sort_name for p in listed] == [
            "Doe, James",
            "Example County DSS",
            "Maple Nursing Care Center",
        ]

    @pytest.mark.asyncio
    async def test_two_does_are_two_people(
        self, async_db_session: AsyncSession
    ) -> None:
        """Identity is the row, never the name. Merging is a decision
        somebody makes, not something a normalizer does on a Tuesday."""
        parties = PartyService(async_db_session)

        first = await parties.create(name="James Doe", kind="person")
        second = await parties.create(name="James Doe", kind="person")
        await async_db_session.commit()

        assert first.id != second.id
        assert len(await parties.find()) == 2

    @pytest.mark.asyncio
    async def test_a_rename_does_not_refile(
        self, async_db_session: AsyncSession
    ) -> None:
        """``sort_name`` is what a reader looks under. Correcting a
        display name is not a statement about where the row belongs,
        which is the whole reason the column is stored rather than
        computed on read."""
        parties = PartyService(async_db_session)
        party = await parties.create(name="Jim Doe", kind="person")
        assert party.sort_name == "Doe, Jim"

        await parties.update(party.id, {"name": "James Doe"})
        await async_db_session.commit()

        assert party.name == "James Doe"
        assert party.sort_name == "Doe, Jim"

    @pytest.mark.asyncio
    async def test_contact_is_a_bag_because_every_party_carries_a_different_one(
        self, async_db_session: AsyncSession
    ) -> None:
        """A county office has a PO box and a fax; a person has a mobile.
        A column per detail is null for everyone it does not apply to."""
        parties = PartyService(async_db_session)
        party = await parties.create(
            name="Example County DSS",
            kind="organization",
            contact={"address": "10 Center St, Anytown NY", "fax": "555-555-0168"},
        )
        await async_db_session.commit()

        assert party.contact["fax"] == "555-555-0168"

    @pytest.mark.asyncio
    async def test_removal_is_soft_because_things_point_here(
        self, async_db_session: AsyncSession
    ) -> None:
        parties = PartyService(async_db_session)
        party = await parties.create(name="James Doe", kind="person")
        await async_db_session.commit()

        assert await parties.remove(party.id) is True
        await async_db_session.commit()

        assert await parties.get(party.id) is None
        assert await parties.find() == []
        # The row is still there, so anything pointing at it still means
        # something.
        assert await async_db_session.get(Party, party.id) is not None

    @pytest.mark.asyncio
    async def test_a_party_needs_a_name_and_a_known_kind(
        self, async_db_session: AsyncSession
    ) -> None:
        parties = PartyService(async_db_session)
        with pytest.raises(ValueError, match="needs a name"):
            await parties.create(name="   ", kind="person")
        with pytest.raises(ValueError, match="One of"):
            await parties.create(name="Someone", kind="robot")

    def test_a_one_word_person_files_as_itself(self) -> None:
        assert sort_name_for("Cher", "person") == "Cher"
