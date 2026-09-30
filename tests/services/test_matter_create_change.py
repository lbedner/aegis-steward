"""She can open a matter (#284).

Asked twice and refused twice: "can you create a case for this? ... a
matter", then "let's create a matter, and call it Marisa's Root
Canals". Everything else about a case - its asks, its events, its
paper - had a card; opening one did not, so a visit with no case had
nowhere to go.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.matters import MatterService
from app.services.matters.opening import (
    OpenMatterPayload,
    open_matter_describe,
    open_matter_execute,
)
from app.services.matters.service import PartyService


async def _party(db: AsyncSession, name: str, kind: str = "person") -> int:
    party = await PartyService(db).create(name=name, kind=kind)
    await db.flush()
    return int(party.id)


class TestOpeningAMatter:
    @pytest.mark.asyncio
    async def test_the_card_says_what_it_will_open(
        self, async_db_session: AsyncSession
    ) -> None:
        marisa = await _party(async_db_session, "Marisa Bedner")
        dentist = await _party(
            async_db_session, "Magdalena Goralczyk, DDS", "organization"
        )
        payload = OpenMatterPayload(
            title="Marisa's Root Canals",
            kind="dental",
            subject_party_id=marisa,
            counterpart_party_id=dentist,
            opened_on=date(2026, 9, 29),
            note="Two root canals, about $1,800, reimbursed from the HSA",
        )

        said = {
            row.label: row.value
            for row in await open_matter_describe(async_db_session, payload, None)
        }

        assert said["Matter"] == "Marisa's Root Canals"
        assert said["About"] == "Marisa Bedner"
        assert said["With"] == "Magdalena Goralczyk, DDS"
        assert said["Opened"] == "2026-09-29"

    @pytest.mark.asyncio
    async def test_approving_it_opens_the_case_with_its_people(
        self, async_db_session: AsyncSession
    ) -> None:
        marisa = await _party(async_db_session, "Marisa Bedner")
        dentist = await _party(
            async_db_session, "Magdalena Goralczyk, DDS", "organization"
        )

        done = await open_matter_execute(
            async_db_session,
            OpenMatterPayload(
                title="Marisa's Root Canals",
                subject_party_id=marisa,
                counterpart_party_id=dentist,
            ),
            None,
        )
        await async_db_session.commit()

        matters = MatterService(async_db_session)
        matter = await matters.get(done["matter_id"])
        assert matter is not None and matter.status == "open"
        assert matter.subject_party_id == marisa
        # who it is ABOUT and who it is WITH are participants too, as the
        # page reads them
        roles = {
            (party.id, link.role)
            for link, party in await matters.participants(matter.id)
        }
        assert roles == {(marisa, "subject"), (dentist, "agency")}

    @pytest.mark.asyncio
    async def test_a_reference_already_on_a_case_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        """The agency's own number is how two letters agree they are one
        case; a second case under it is how the next letter gets lost."""
        await MatterService(async_db_session).open(
            title="Medicaid renewal", reference="MA258760XX"
        )
        await async_db_session.flush()

        with pytest.raises(ValueError, match="Medicaid renewal"):
            await open_matter_describe(
                async_db_session,
                OpenMatterPayload(title="Renewal again", reference="MA258760XX"),
                None,
            )

    @pytest.mark.asyncio
    async def test_somebody_not_on_file_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        with pytest.raises(ValueError, match="999999"):
            await open_matter_describe(
                async_db_session,
                OpenMatterPayload(title="A case", subject_party_id=999999),
                None,
            )

    def test_a_matter_needs_a_title(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            OpenMatterPayload(title="   ")


def test_it_is_registered_and_advertised() -> None:
    from app.services.finance.domains import writes
    from app.services.finance.domains.detection.analyst.prompt_changes import (
        PROPOSING_CHANGES,
    )

    assert "matter.create" in writes.registered_change_types()
    assert "`matter.create`" in PROPOSING_CHANGES
