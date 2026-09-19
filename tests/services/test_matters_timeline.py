"""ST-10: the story of a matter, in date order.

Three years from now the papers will still be there and the story will
not. Most of it is already dated rows - a request arrived, a figure is
as of a date, a paper answered an ask - so the timeline is DERIVED from
them and can never disagree with them. What is missing is the part that
leaves no paper: the phone call, the mailing, the office visit.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.matters import MatterService
from app.services.matters.requests import RequestService
from app.services.matters.timeline import MatterEventService, timeline


async def _matter(db: AsyncSession, reference: str) -> int:
    matter = await MatterService(db).open(
        title="Medicaid renewal", reference=reference, opened_on=date(2026, 8, 20)
    )
    return matter.id


class TestTheStoryIsDerived:
    @pytest.mark.asyncio
    async def test_it_reads_in_date_order_from_rows_nobody_retyped(
        self, async_db_session: AsyncSession
    ) -> None:
        matter_id = await _matter(async_db_session, "TL-1")
        await RequestService(async_db_session).record(
            matter_id=matter_id,
            received_on=date(2026, 8, 27),
            due_on=date(2026, 9, 21),
            items=[{"asked": "Proof of gross monthly income"}],
        )
        await async_db_session.commit()

        story = await timeline(async_db_session, matter_id)

        assert [moment["on"] for moment in story] == sorted(
            moment["on"] for moment in story
        )
        assert [moment["kind"] for moment in story] == ["opened", "asked", "due"]
        assert "Medicaid renewal" in story[0]["what"]

    @pytest.mark.asyncio
    async def test_a_figure_takes_the_date_it_is_as_of(
        self, async_db_session: AsyncSession
    ) -> None:
        """A figure read in September about the first of August belongs
        where the county will look for it: August."""
        from app.services.matters.facts import FactService
        from app.services.matters.service import PartyService

        matter_id = await _matter(async_db_session, "TL-2")
        subject = await PartyService(async_db_session).create(
            name="Timeline Testcase", kind="person"
        )
        await FactService(async_db_session).record(
            matter_id=matter_id,
            subject_party_id=subject.id,
            attribute="account_balance",
            value_cents=313744,
            as_of=date(2026, 8, 1),
        )
        await async_db_session.commit()

        story = await timeline(async_db_session, matter_id)
        figure = [m for m in story if m["kind"] == "figure"]
        assert [m["on"] for m in figure] == [date(2026, 8, 1)]
        assert "$3,137.44" in figure[0]["what"]

    @pytest.mark.asyncio
    async def test_an_ask_is_answered_once_however_many_pages_prove_it(
        self, async_db_session: AsyncSession
    ) -> None:
        """A statement attached on Monday and its second page on Friday
        is one ask answered on Monday, not an ask that keeps being
        answered."""
        from app.services.matters.evidence import link
        from app.services.matters.facts import FactService
        from app.services.matters.service import PartyService

        matter_id = await _matter(async_db_session, "TL-7")
        request = await RequestService(async_db_session).record(
            matter_id=matter_id,
            received_on=date(2026, 8, 27),
            items=[{"asked": "Proof of gross monthly income"}],
        )
        items = await RequestService(async_db_session).items(request.id)
        subject = await PartyService(async_db_session).create(
            name="Answered Testcase", kind="person"
        )
        figure = await FactService(async_db_session).record(
            subject_party_id=subject.id,
            matter_id=matter_id,
            attribute="gross_income",
            value_cents=217894,
            as_of=date(2026, 9, 1),
        )
        await async_db_session.flush()
        await link(async_db_session, items[0].id, fact_id=figure.id)
        await link(async_db_session, items[0].id, document_id=None, fact_id=figure.id)
        await async_db_session.commit()

        story = await timeline(async_db_session, matter_id)
        answered = [m for m in story if m["kind"] == "answered"]
        assert len(answered) == 1
        assert answered[0]["item_id"] == items[0].id
        assert "Proof of gross monthly income" in answered[0]["what"]

    @pytest.mark.asyncio
    async def test_a_matter_with_nothing_in_it_has_no_story(
        self, async_db_session: AsyncSession
    ) -> None:
        assert await timeline(async_db_session, 999999) == []


class TestTheEventsThatLeaveNoPaper:
    @pytest.mark.asyncio
    async def test_a_call_places_itself_among_the_rest(
        self, async_db_session: AsyncSession
    ) -> None:
        matter_id = await _matter(async_db_session, "TL-3")
        await RequestService(async_db_session).record(
            matter_id=matter_id,
            received_on=date(2026, 8, 27),
            items=[{"asked": "Proof of gross monthly income"}],
        )
        event = await MatterEventService(async_db_session).add(
            matter_id=matter_id,
            occurred_at=date(2026, 8, 25),
            kind="call",
            summary="Called DSS, confirmed receipt",
        )
        await async_db_session.commit()

        story = await timeline(async_db_session, matter_id)
        assert [m["kind"] for m in story] == ["opened", "call", "asked"]
        assert story[1]["what"] == "Called DSS, confirmed receipt"
        assert story[1]["event_id"] == event.id

    @pytest.mark.asyncio
    async def test_removing_the_event_changes_nothing_else(
        self, async_db_session: AsyncSession
    ) -> None:
        matter_id = await _matter(async_db_session, "TL-4")
        events = MatterEventService(async_db_session)
        event = await events.add(
            matter_id=matter_id,
            occurred_at=date(2026, 8, 25),
            kind="mailed",
            summary="Mailed the POA",
        )
        await async_db_session.commit()
        before = await timeline(async_db_session, matter_id)

        await events.remove(event.id)
        await async_db_session.commit()

        after = await timeline(async_db_session, matter_id)
        assert [m["kind"] for m in before] == ["opened", "mailed"]
        assert [m["kind"] for m in after] == ["opened"]

    @pytest.mark.asyncio
    async def test_a_kind_nobody_defined_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        matter_id = await _matter(async_db_session, "TL-5")
        with pytest.raises(ValueError, match="call"):
            await MatterEventService(async_db_session).add(
                matter_id=matter_id,
                occurred_at=date(2026, 8, 25),
                kind="telepathy",
                summary="Thought about it",
            )

    @pytest.mark.asyncio
    async def test_an_event_about_nothing_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        matter_id = await _matter(async_db_session, "TL-6")
        with pytest.raises(ValueError, match="say"):
            await MatterEventService(async_db_session).add(
                matter_id=matter_id,
                occurred_at=date(2026, 8, 25),
                kind="call",
                summary="   ",
            )


class TestSheCanRecordWhatHappened:
    """The timeline's whole point is the part that leaves no paper, and
    that part is exactly what somebody TELLS you rather than files. A
    person who has just said "I called DSS" out loud is not going to
    open a dialog and type it again."""

    @pytest.mark.asyncio
    async def test_the_card_says_what_it_will_write(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.service import PartyService
        from app.services.matters.timeline import (
            RecordEventPayload,
            record_event_describe,
            record_event_execute,
        )

        matter_id = await _matter(async_db_session, "TL-8")
        dss = await PartyService(async_db_session).create(
            name="Dutchess County DSS", kind="organization"
        )
        await async_db_session.flush()
        payload = RecordEventPayload(
            matter_id=matter_id,
            occurred_at=date(2026, 9, 18),
            kind="call",
            summary="Called DSS, confirmed they have the packet",
            party_id=dss.id,
        )

        said = {
            row.label: row.value
            for row in await record_event_describe(async_db_session, payload, None)
        }
        assert said["Matter"] == "Medicaid renewal"
        assert said["What"] == "Called DSS, confirmed they have the packet"
        assert said["With"] == "Dutchess County DSS"

        await record_event_execute(async_db_session, payload, None)
        await async_db_session.commit()
        story = await timeline(async_db_session, matter_id)
        assert [m["kind"] for m in story] == ["opened", "call"]

    @pytest.mark.asyncio
    async def test_a_matter_that_is_not_there_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.timeline import (
            RecordEventPayload,
            record_event_describe,
        )

        with pytest.raises(ValueError, match="999999"):
            await record_event_describe(
                async_db_session,
                RecordEventPayload(
                    matter_id=999999,
                    occurred_at=date(2026, 9, 18),
                    kind="call",
                    summary="Called about nothing",
                ),
                None,
            )

    def test_a_kind_nobody_defined_is_refused_at_the_payload(self) -> None:
        from pydantic import ValidationError

        from app.services.matters.timeline import RecordEventPayload

        with pytest.raises(ValidationError):
            RecordEventPayload(
                matter_id=1,
                occurred_at=date(2026, 9, 18),
                kind="telepathy",
                summary="Thought about it",
            )

    def test_it_is_registered_as_a_change_type(self) -> None:
        """Registered, or she can propose it and nothing can execute it."""
        from app.services.finance.domains import writes

        assert "matter.event" in writes.registered_change_types()
