"""The matter: the case a document belongs to.

ST-03's gate is the Medicaid case - it exists once with its real case
number, names James as subject, DSS as agency, Maple as facility and
the owner as representative, and a SECOND letter from the same agency
lands on the same matter rather than starting a new one.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.matters import MatterService, summarised
from app.services.matters.service import PartyService


async def _cast(db: AsyncSession) -> dict[str, int]:
    parties = PartyService(db)
    return {
        "james": (await parties.create(name="James Doe", kind="person")).id,
        "dss": (
            await parties.create(name="Example County DSS", kind="organization")
        ).id,
        "maple": (
            await parties.create(name="Maple Nursing Care Center", kind="organization")
        ).id,
        "sam": (await parties.create(name="Sam Doe", kind="person")).id,
    }


class TestAMatter:
    @pytest.mark.asyncio
    async def test_the_medicaid_case(self, async_db_session: AsyncSession) -> None:
        cast = await _cast(async_db_session)
        matters = MatterService(async_db_session)

        case = await matters.open(
            title="Medicaid renewal",
            kind="medicaid",
            reference="MA123456XX",
            subject_party_id=cast["james"],
            counterpart_party_id=cast["dss"],
            opened_on=date(2026, 8, 20),
        )
        # the subject and the agency joined as the case opened; the rest
        # are added
        for party, role in (
            ("maple", "facility"),
            ("sam", "representative"),
        ):
            await matters.add_participant(case.id, cast[party], role)
        await async_db_session.commit()

        drawn = await summarised(async_db_session, case)
        assert drawn["reference"] == "MA123456XX"
        assert drawn["status"] == "open"
        assert {(p["role"], p["party"]) for p in drawn["participants"]} == {
            ("subject", "James Doe"),
            ("agency", "Example County DSS"),
            ("facility", "Maple Nursing Care Center"),
            ("representative", "Sam Doe"),
        }

    @pytest.mark.asyncio
    async def test_a_second_letter_lands_on_the_same_case(
        self, async_db_session: AsyncSession
    ) -> None:
        """The agency's own case number is the one thing two letters
        agree about, which is why it is what we look a matter up by."""
        matters = MatterService(async_db_session)
        first = await matters.open(title="Medicaid renewal", reference="MA123456XX")
        await async_db_session.commit()

        found = await matters.by_reference(" MA123456XX ")

        assert found is not None and found.id == first.id
        assert await matters.by_reference("MA000000XX") is None

    @pytest.mark.asyncio
    async def test_a_role_is_per_matter_not_per_party(
        self, async_db_session: AsyncSession
    ) -> None:
        """Maple is the facility in this case and a payee in the
        ledger. Neither fact belongs to the other, which is why ST-01
        left the role off the party."""
        cast = await _cast(async_db_session)
        matters = MatterService(async_db_session)
        renewal = await matters.open(title="Medicaid renewal")
        estate = await matters.open(title="Estate")
        await matters.add_participant(renewal.id, cast["maple"], "facility")
        await matters.add_participant(estate.id, cast["maple"], "other")
        await async_db_session.commit()

        assert [
            p["role"]
            for p in (await summarised(async_db_session, renewal))["participants"]
        ] == ["facility"]
        assert [
            p["role"]
            for p in (await summarised(async_db_session, estate))["participants"]
        ] == ["other"]

    @pytest.mark.asyncio
    async def test_one_party_can_be_two_things_in_one_matter(
        self, async_db_session: AsyncSession
    ) -> None:
        """A representative who is also the subject's son. Only
        (matter, party, role) is unique."""
        cast = await _cast(async_db_session)
        matters = MatterService(async_db_session)
        case = await matters.open(title="Medicaid renewal")
        await matters.add_participant(case.id, cast["sam"], "representative")
        await matters.add_participant(case.id, cast["sam"], "other")
        await async_db_session.commit()

        roles = [
            p["role"]
            for p in (await summarised(async_db_session, case))["participants"]
        ]
        assert sorted(roles) == ["other", "representative"]

    @pytest.mark.asyncio
    async def test_a_letter_says_who_wrote_it_and_who_it_is_about(
        self, async_db_session: AsyncSession
    ) -> None:
        """Provenance in the sense of WHO, not only when. "A letter from
        the county about James" is the thing somebody searches for."""
        cast = await _cast(async_db_session)
        matters = MatterService(async_db_session)
        await matters.name_document(7, cast["dss"], "sender")
        await matters.name_document(7, cast["james"], "subject")
        await async_db_session.commit()

        named = dict(
            (role, party.name) for role, party in await matters.document_parties(7)
        )
        assert named == {
            "sender": "Example County DSS",
            "subject": "James Doe",
        }

    @pytest.mark.asyncio
    async def test_status_is_open_or_closed_and_nothing_else(
        self, async_db_session: AsyncSession
    ) -> None:
        """No workflow engine. What must happen next is a request, not a
        status - a status that tries to say it drifts out of step with
        the letters that decide it."""
        matters = MatterService(async_db_session)
        case = await matters.open(title="Medicaid renewal")
        await async_db_session.commit()

        with pytest.raises(ValueError, match="One of"):
            await matters.set_status(case.id, "awaiting_documents")

        await matters.set_status(case.id, "closed", on=date(2026, 9, 1))
        assert case.status == "closed" and case.closed_on == date(2026, 9, 1)

    @pytest.mark.asyncio
    async def test_a_matter_needs_a_title_and_a_known_role(
        self, async_db_session: AsyncSession
    ) -> None:
        matters = MatterService(async_db_session)
        with pytest.raises(ValueError, match="needs a title"):
            await matters.open(title="  ")
        case = await matters.open(title="Medicaid renewal")
        with pytest.raises(ValueError, match="One of"):
            await matters.add_participant(case.id, 1, "landlord")
