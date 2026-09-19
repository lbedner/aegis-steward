"""ST-09: an open request nags BEFORE it is late, not after.

The deadline already exists on the request and the sidebar already shows
a dot once it has passed. That is the wrong moment: a county renewal
missed by two days is a benefit stopped, and "it went red this morning"
is not a warning, it is a post-mortem.

So the deadline surfaces where every other thing needing attention
already lives - the insight list - and it is retracted the moment the
request is satisfied, because an alert nobody can clear is an alert
everybody learns to scroll past.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.deadlines import nag
from app.services.matters.matters import MatterService
from app.services.matters.requests import RequestService

TODAY = date(2026, 9, 1)


async def _request_due(db: AsyncSession, reference: str, due: date | None):
    matter = await MatterService(db).open(title="Medicaid renewal", reference=reference)
    return await RequestService(db).record(
        matter_id=matter.id,
        received_on=date(2026, 8, 20),
        due_on=due,
        items=[{"asked": "Proof of gross monthly income"}],
    )


async def _said(db: AsyncSession) -> list[tuple[str, str]]:
    from sqlmodel import col, select

    from app.services.finance.models import FinanceInsight

    rows = (
        await db.exec(
            select(FinanceInsight)
            .where(col(FinanceInsight.insight_type) == "matter_due")
            .where(col(FinanceInsight.status) != "dismissed")
        )
    ).all()
    return [(row.severity, row.title) for row in rows]


class TestItNagsBeforeItIsLate:
    @pytest.mark.asyncio
    async def test_a_deadline_inside_the_window_is_raised(
        self, async_db_session: AsyncSession
    ) -> None:
        await _request_due(async_db_session, "DL-1", date(2026, 9, 10))
        await nag(async_db_session, owner_user_id=None, today=TODAY)
        await async_db_session.commit()

        said = await _said(async_db_session)
        assert [severity for severity, _title in said] == ["warning"]
        assert "Medicaid renewal" in said[0][1]

    @pytest.mark.asyncio
    async def test_one_already_past_is_critical(
        self, async_db_session: AsyncSession
    ) -> None:
        await _request_due(async_db_session, "DL-2", date(2026, 8, 25))
        await nag(async_db_session, owner_user_id=None, today=TODAY)
        await async_db_session.commit()

        assert [severity for severity, _t in await _said(async_db_session)] == [
            "critical"
        ]

    @pytest.mark.asyncio
    async def test_one_far_off_is_left_alone(
        self, async_db_session: AsyncSession
    ) -> None:
        """A renewal due in three months is not news today, and an alert
        that arrives too early is one somebody dismisses while it is
        still true."""
        await _request_due(async_db_session, "DL-3", date(2026, 12, 1))
        await nag(async_db_session, owner_user_id=None, today=TODAY)
        await async_db_session.commit()

        assert await _said(async_db_session) == []

    @pytest.mark.asyncio
    async def test_a_request_with_no_deadline_says_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        await _request_due(async_db_session, "DL-4", None)
        await nag(async_db_session, owner_user_id=None, today=TODAY)
        await async_db_session.commit()

        assert await _said(async_db_session) == []

    @pytest.mark.asyncio
    async def test_running_twice_raises_one_alert(
        self, async_db_session: AsyncSession
    ) -> None:
        await _request_due(async_db_session, "DL-5", date(2026, 9, 10))
        await nag(async_db_session, owner_user_id=None, today=TODAY)
        await nag(async_db_session, owner_user_id=None, today=TODAY)
        await async_db_session.commit()

        assert len(await _said(async_db_session)) == 1

    @pytest.mark.asyncio
    async def test_it_sharpens_when_the_day_passes(
        self, async_db_session: AsyncSession
    ) -> None:
        """The same deadline, now missed, is a different fact - and the
        warning that said "due in 9 days" must not be what the reader is
        still looking at."""
        await _request_due(async_db_session, "DL-6", date(2026, 9, 2))
        await nag(async_db_session, owner_user_id=None, today=TODAY)
        await nag(async_db_session, owner_user_id=None, today=date(2026, 9, 5))
        await async_db_session.commit()

        assert [severity for severity, _t in await _said(async_db_session)] == [
            "critical"
        ]


class TestItStopsWhenTheWorkIsDone:
    @pytest.mark.asyncio
    async def test_satisfying_the_request_retracts_the_alert(
        self, async_db_session: AsyncSession
    ) -> None:
        """An alert nobody can clear is an alert everybody learns to
        scroll past."""
        request = await _request_due(async_db_session, "DL-7", date(2026, 9, 10))
        await nag(async_db_session, owner_user_id=None, today=TODAY)
        await async_db_session.commit()
        assert await _said(async_db_session)

        items = await RequestService(async_db_session).items(request.id)
        await RequestService(async_db_session).mark(items[0].id, "satisfied")
        await nag(async_db_session, owner_user_id=None, today=TODAY)
        await async_db_session.commit()

        assert await _said(async_db_session) == []
