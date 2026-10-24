"""She can change a matter already on file (#566).

Asked whether a contractor's coming visit could go on a case, she said a
matter "can hold a note" and could not put one there: ``matter.create``
wrote the note once and nothing changed it after. ``matter.amend`` is
``contact.amend``'s shape: only what is sent changes, "" clears it.
"""

from pydantic import ValidationError
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.analyst.prompt_matters import (
    MATTER_CHANGES,
)
import app.services.finance.domains.writes.executors  # noqa: F401 - registers them
from app.services.finance.domains.writes.registry import registered_change_types
from app.services.matters.matters import MatterService
from app.services.matters.opening import (
    AmendMatterPayload,
    amend_matter_describe,
    amend_matter_execute,
)


async def _matter(
    db: AsyncSession,
    kind: str | None = None,
    reference: str | None = None,
    note: str | None = None,
) -> int:
    matter = await MatterService(db).open(
        title="Water heater", kind=kind, reference=reference, note=note
    )
    await db.flush()
    return int(matter.id)


class TestAmendingAMatter:
    @pytest.mark.asyncio
    async def test_the_card_says_what_changes_and_nothing_else(
        self, async_db_session: AsyncSession
    ) -> None:
        matter_id = await _matter(async_db_session, kind="repair", note="Leaking")
        payload = AmendMatterPayload(
            matter_id=matter_id,
            note="Plumber coming Thursday to fix it",
            kind="repair",
        )

        said = {
            row.label: row.value
            for row in await amend_matter_describe(async_db_session, payload, None)
        }

        assert said == {
            "Matter": "Water heater",
            "Note": "Leaking → Plumber coming Thursday to fix it",
        }

    @pytest.mark.asyncio
    async def test_approving_it_changes_only_what_was_sent(
        self, async_db_session: AsyncSession
    ) -> None:
        matter_id = await _matter(
            async_db_session, kind="repair", reference="WO-1", note="Leaking"
        )

        await amend_matter_execute(
            async_db_session,
            AmendMatterPayload(
                matter_id=matter_id, note="Thursday visit", reference=""
            ),
            None,
        )
        await async_db_session.commit()

        matter = await MatterService(async_db_session).require(matter_id)
        assert matter.note == "Thursday visit"
        assert matter.reference is None  # "" clears it
        assert matter.kind == "repair"  # not sent, not touched
        assert matter.title == "Water heater"

    @pytest.mark.asyncio
    async def test_a_card_that_changes_nothing_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        matter_id = await _matter(async_db_session, note="Leaking")

        with pytest.raises(ValueError, match="change nothing"):
            await amend_matter_describe(
                async_db_session,
                AmendMatterPayload(matter_id=matter_id, note="Leaking"),
                None,
            )

    @pytest.mark.asyncio
    async def test_a_reference_another_case_carries_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        await MatterService(async_db_session).open(
            title="Medicaid renewal", reference="MA123456XX"
        )
        matter_id = await _matter(async_db_session)

        with pytest.raises(ValueError, match="Medicaid renewal"):
            await amend_matter_describe(
                async_db_session,
                AmendMatterPayload(matter_id=matter_id, reference="MA123456XX"),
                None,
            )

    def test_it_must_say_something_and_keep_a_title(self) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            AmendMatterPayload(matter_id=1)
        with pytest.raises(ValidationError, match="title"):
            AmendMatterPayload(matter_id=1, title="  ")

    def test_she_is_told_she_can(self) -> None:
        """Registered and advertised are two steps; a type she is never
        told about is one she says she cannot do."""
        assert "matter.amend" in registered_change_types()
        assert "`matter.amend`" in MATTER_CHANGES
