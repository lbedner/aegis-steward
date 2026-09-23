"""ST-11: a matter says when its next request is due to arrive.

A Medicaid renewal comes every year, so the one thing certain about next
August is that another letter asks for the same proof - and last time it
gave eight days. Deadlines already nag two weeks ahead of a request that
EXISTS; this nags two months ahead of one that is coming, long enough to
have a pension's gross-income letter in hand before the county asks.

It rides the deadline rule rather than growing a second one: the same
insight type, so the same retraction, the same Attention queue, banner
and briefing.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.deadlines import expected_soon, nag
from app.services.matters.matters import MatterService
from app.services.matters.requests import RequestService

EXPECTED = date(2027, 8, 1)


async def _renewal(db: AsyncSession, **cadence) -> int:
    matters = MatterService(db)
    matter = await matters.open(title="Medicaid renewal", reference="MA-CAD-1")
    await matters.set_cadence(int(matter.id), **cadence)
    return int(matter.id)


async def _said(db: AsyncSession) -> list[tuple[str, str]]:
    from sqlmodel import col, select

    from app.services.finance.models import FinanceInsight

    rows = (
        await db.exec(
            select(FinanceInsight).where(
                col(FinanceInsight.insight_type) == "matter_due"
            )
        )
    ).all()
    return [(row.severity, row.title) for row in rows]


class TestItSaysWhenTheNextLetterIsDue:
    @pytest.mark.asyncio
    async def test_nothing_shows_until_sixty_days_before(
        self, async_db_session: AsyncSession
    ) -> None:
        await _renewal(async_db_session, cadence="annual", next_expected_on=EXPECTED)
        await nag(async_db_session, owner_user_id=None, today=date(2027, 6, 1))
        assert await _said(async_db_session) == []

    @pytest.mark.asyncio
    async def test_then_it_names_the_matter_and_the_month(
        self, async_db_session: AsyncSession
    ) -> None:
        await _renewal(async_db_session, cadence="annual", next_expected_on=EXPECTED)
        await nag(async_db_session, owner_user_id=None, today=date(2027, 6, 2))
        assert await _said(async_db_session) == [
            ("info", "Medicaid renewal: next request expected Aug 2027")
        ]

    @pytest.mark.asyncio
    async def test_running_twice_raises_one_alert(
        self, async_db_session: AsyncSession
    ) -> None:
        await _renewal(async_db_session, cadence="annual", next_expected_on=EXPECTED)
        for _ in range(2):
            await nag(async_db_session, owner_user_id=None, today=date(2027, 7, 1))
        assert len(await _said(async_db_session)) == 1

    @pytest.mark.asyncio
    async def test_snoozing_is_moving_the_date(
        self, async_db_session: AsyncSession
    ) -> None:
        """There is no reminder state to rot: the date is the reminder."""
        matter_id = await _renewal(
            async_db_session, cadence="annual", next_expected_on=EXPECTED
        )
        await nag(async_db_session, owner_user_id=None, today=date(2027, 7, 1))
        await MatterService(async_db_session).set_cadence(
            matter_id, cadence="annual", next_expected_on=date(2027, 10, 1)
        )
        await nag(async_db_session, owner_user_id=None, today=date(2027, 7, 1))
        assert await _said(async_db_session) == []

    @pytest.mark.asyncio
    async def test_a_closed_matter_expects_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        matter_id = await _renewal(
            async_db_session, cadence="annual", next_expected_on=EXPECTED
        )
        await MatterService(async_db_session).set_status(matter_id, "closed")
        assert await expected_soon(async_db_session, today=date(2027, 7, 1)) == []


class TestTheLetterArriving:
    @pytest.mark.asyncio
    async def test_it_clears_the_expectation_and_sets_the_next_one(
        self, async_db_session: AsyncSession
    ) -> None:
        """The gate: marking the new request received clears it and moves
        the next expected date on by the cadence."""
        db = async_db_session
        matter_id = await _renewal(db, cadence="annual", next_expected_on=EXPECTED)
        await nag(db, owner_user_id=None, today=date(2027, 7, 20))
        assert len(await _said(db)) == 1

        await RequestService(db).record(
            matter_id=matter_id, received_on=date(2027, 7, 28)
        )
        await nag(db, owner_user_id=None, today=date(2027, 7, 28))

        assert (await MatterService(db).get(matter_id)).next_expected_on == date(
            2028, 8, 1
        )
        assert await _said(db) == []

    @pytest.mark.asyncio
    async def test_a_letter_that_is_not_the_expected_one_moves_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        """A follow-up letter in March is not next August's renewal."""
        db = async_db_session
        matter_id = await _renewal(db, cadence="annual", next_expected_on=EXPECTED)
        await RequestService(db).record(
            matter_id=matter_id, received_on=date(2027, 3, 1)
        )
        assert (await MatterService(db).get(matter_id)).next_expected_on == EXPECTED

    @pytest.mark.asyncio
    async def test_a_one_off_expectation_is_simply_cleared(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        matter_id = await _renewal(db, cadence=None, next_expected_on=EXPECTED)
        await RequestService(db).record(
            matter_id=matter_id, received_on=date(2027, 7, 28)
        )
        assert (await MatterService(db).get(matter_id)).next_expected_on is None


class TestWhatACadenceCanBe:
    @pytest.mark.asyncio
    async def test_an_unknown_cadence_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        matter = await MatterService(async_db_session).open(title="Tax")
        with pytest.raises(ValueError):
            await MatterService(async_db_session).set_cadence(
                int(matter.id), cadence="whenever", next_expected_on=None
            )

    @pytest.mark.parametrize(
        ("cadence", "after"),
        [
            ("annual", date(2028, 8, 1)),
            ("semiannual", date(2028, 2, 1)),
            ("quarterly", date(2027, 11, 1)),
        ],
    )
    def test_each_cadence_moves_the_date_on(self, cadence: str, after: date) -> None:
        from app.services.matters.matters import next_after

        assert next_after(EXPECTED, cadence) == after


class TestTheSuggestedDate:
    """Picking a cadence suggests the date: agencies send renewals on a
    cycle, so the next one comes about a cadence after the last one
    ARRIVED. A suggestion, not a write - the dialog shows it, the person
    saves it."""

    @pytest.mark.asyncio
    async def test_a_cadence_after_the_last_letter_arrived(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.matters import suggested_next

        db = async_db_session
        matter = await MatterService(db).open(
            title="Medicaid renewal", reference="MA-SUG-1"
        )
        await RequestService(db).record(
            matter_id=int(matter.id), received_on=date(2025, 8, 12)
        )
        await RequestService(db).record(
            matter_id=int(matter.id), received_on=date(2026, 8, 20)
        )

        assert await suggested_next(
            db, int(matter.id), "annual", today=date(2026, 9, 22)
        ) == date(2027, 8, 20)

    @pytest.mark.asyncio
    async def test_a_date_already_past_steps_forward(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.matters import suggested_next

        db = async_db_session
        matter = await MatterService(db).open(title="Review", reference="MA-SUG-2")
        await RequestService(db).record(
            matter_id=int(matter.id), received_on=date(2026, 1, 10)
        )

        assert await suggested_next(
            db, int(matter.id), "quarterly", today=date(2026, 9, 22)
        ) == date(2026, 10, 10)

    @pytest.mark.asyncio
    async def test_no_letter_falls_back_to_when_the_matter_opened(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.matters import suggested_next

        db = async_db_session
        matter = await MatterService(db).open(
            title="Recert", reference="MA-SUG-3", opened_on=date(2026, 3, 1)
        )
        assert await suggested_next(
            db, int(matter.id), "semiannual", today=date(2026, 9, 22)
        ) == date(2027, 3, 1)

    @pytest.mark.asyncio
    async def test_nothing_to_count_from_suggests_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.matters import suggested_next

        matter = await MatterService(async_db_session).open(
            title="Tax", reference="MA-SUG-4"
        )
        assert await suggested_next(async_db_session, int(matter.id), "annual") is None
