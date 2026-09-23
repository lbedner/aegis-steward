"""Goals and envelopes as cards: the assistant proposes, a person decides.

Bills and income already went through the queue; the plan's other half
did not, so the assistant could see a wrong goal and not fix it (#166).
None of these moves money - they change what the plan assumes, and the
card says what the forecast will do about it.
"""

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession


async def _said(describe: Any, db: AsyncSession, payload: Any) -> dict[str, str]:
    return {row.label: row.value for row in await describe(db, payload, None)}


async def _goal(db: AsyncSession, **fields: Any) -> Any:
    from app.services.finance.service import FinanceService

    return await FinanceService(db).create_virtual_goal(
        owner_user_id=None, name="Vacation", target_amount=300_000, **fields
    )


async def _envelope(db: AsyncSession) -> Any:
    from app.services.finance.service import FinanceService

    return await FinanceService(db).create_envelope(
        owner_user_id=None, name="Vanessa's", monthly_credit=1_000, cadence="weekly"
    )


class TestGoals:
    @pytest.mark.asyncio
    async def test_a_new_goal_says_what_the_forecast_sets_aside(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.planning.goals import goal_metadata
        from app.services.finance.domains.writes.planning import (
            GoalCreatePayload,
            goal_create_describe,
            goal_create_execute,
        )

        payload = GoalCreatePayload(
            name="Emergency fund",
            target_cents=1_000_000,
            monthly_cents=25_000,
        )
        said = await _said(goal_create_describe, async_db_session, payload)
        assert said["Goal"] == "Emergency fund"
        # No date, so the declared amount is the ask.
        assert said["Target"] == "$10,000.00"
        assert said["Forecast"] == "sets aside $250.00 on the 1st of each month"

        made = await goal_create_execute(async_db_session, payload, None)
        from app.services.finance.service import FinanceService

        account = await FinanceService(async_db_session).get_account(
            made["account_id"], owner_user_id=None
        )
        meta = goal_metadata(account.metadata_)
        assert (meta.target_amount, meta.monthly_contribution) == (1_000_000, 25_000)

    @pytest.mark.asyncio
    async def test_editing_a_goal_shows_before_and_after(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.planning.goals import goal_metadata
        from app.services.finance.domains.writes.planning import (
            GoalUpdatePayload,
            goal_update_describe,
            goal_update_execute,
        )

        goal = await _goal(async_db_session, monthly_contribution=10_000)
        payload = GoalUpdatePayload(account_id=goal.id, monthly_cents=25_000)

        said = await _said(goal_update_describe, async_db_session, payload)
        assert said["Goal"] == "Vacation"
        assert said["Each month"] == "$100.00 → $250.00"
        assert said["Forecast"] == "sets aside $250.00 on the 1st of each month"

        await goal_update_execute(async_db_session, payload, None)
        await async_db_session.refresh(goal)
        meta = goal_metadata(goal.metadata_)
        # Only what was sent changes.
        assert (meta.monthly_contribution, meta.target_amount) == (25_000, 300_000)

    @pytest.mark.asyncio
    async def test_a_target_date_decides_what_the_forecast_sets_aside(
        self, async_db_session: AsyncSession
    ) -> None:
        """Live: the Vacation card said "sets aside $100.00" and the Budget
        page said $300.00/mo. With a target date the engine asks what the
        date needs ($3,000 over 10 months) and the declared $100 is not
        used; the card had done its own sum instead of asking (2026-09-23)."""
        from app.services.finance.constants import add_months
        from app.services.finance.domains.writes.planning import (
            GoalUpdatePayload,
            goal_update_describe,
        )
        from app.services.finance.utils import current_date

        today = current_date()
        ten_months = add_months(today.replace(day=1), 10)
        goal = await _goal(
            async_db_session, target_date=ten_months, monthly_contribution=None
        )
        payload = GoalUpdatePayload(account_id=goal.id, monthly_cents=10_000)

        said = await _said(goal_update_describe, async_db_session, payload)
        assert said["Forecast"].startswith("sets aside $300.00 on the 1st")
        assert "$100.00 a month is not used while it has a date" in said["Forecast"]

    @pytest.mark.asyncio
    async def test_pausing_a_goal_says_the_forecast_stops_asking(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.planning.goals import goal_metadata
        from app.services.finance.domains.writes.planning import (
            GoalUpdatePayload,
            goal_update_describe,
            goal_update_execute,
        )

        goal = await _goal(async_db_session, monthly_contribution=10_000)
        payload = GoalUpdatePayload(account_id=goal.id, status="paused")

        said = await _said(goal_update_describe, async_db_session, payload)
        assert said["Status"] == "active → paused"
        assert said["Forecast"] == "sets nothing aside while it is paused"

        await goal_update_execute(async_db_session, payload, None)
        await async_db_session.refresh(goal)
        assert goal_metadata(goal.metadata_).status == "paused"

    @pytest.mark.asyncio
    async def test_a_card_that_changes_nothing_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.planning import (
            GoalUpdatePayload,
            goal_update_describe,
        )

        goal = await _goal(async_db_session, monthly_contribution=10_000)
        with pytest.raises(ValueError, match="nothing"):
            await goal_update_describe(
                async_db_session,
                GoalUpdatePayload(account_id=goal.id, monthly_cents=10_000),
                None,
            )

    @pytest.mark.asyncio
    async def test_an_account_that_is_not_a_goal_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.planning import (
            GoalUpdatePayload,
            goal_update_describe,
        )

        envelope = await _envelope(async_db_session)
        with pytest.raises(ValueError, match="not a goal"):
            await goal_update_describe(
                async_db_session,
                GoalUpdatePayload(account_id=envelope.id, priority=1),
                None,
            )


class TestEnvelopes:
    @pytest.mark.asyncio
    async def test_an_allowance_change_says_it_moves_no_money(
        self, async_db_session: AsyncSession
    ) -> None:
        """The assistant's own words: a $10 weekly envelope credit is not a
        bank transfer unless that is how you intend it to operate."""
        from app.services.finance.domains.planning.envelopes import (
            envelope_metadata,
        )
        from app.services.finance.domains.writes.planning import (
            EnvelopeUpdatePayload,
            envelope_update_describe,
            envelope_update_execute,
        )

        envelope = await _envelope(async_db_session)
        payload = EnvelopeUpdatePayload(account_id=envelope.id, allowance_cents=1_500)

        said = await _said(envelope_update_describe, async_db_session, payload)
        assert said["Envelope"] == "Vanessa's"
        assert said["Allowance"] == "$10.00 → $15.00 a week (about $65.22 a month)"
        assert said["Money"] == "moves none: it is what the plan allows, not a transfer"

        await envelope_update_execute(async_db_session, payload, None)
        await async_db_session.refresh(envelope)
        meta = envelope_metadata(envelope.metadata_)
        # The cadence it did not mention is kept.
        assert (meta.monthly_credit, meta.cadence) == (1_500, "weekly")

    @pytest.mark.asyncio
    async def test_the_balance_can_be_set_to_what_she_says_she_has(
        self, async_db_session: AsyncSession
    ) -> None:
        """Live: "she just told me she has $10 in", and the assistant could
        only change the allowance, not the balance (2026-09-23)."""
        from app.services.finance.domains.writes.planning import (
            EnvelopeBalancePayload,
            envelope_balance_describe,
            envelope_balance_execute,
        )
        from app.services.finance.service import FinanceService

        envelope = await _envelope(async_db_session)
        await FinanceService(async_db_session).credit_envelope(
            envelope.id, amount=6_000, owner_user_id=None
        )
        payload = EnvelopeBalancePayload(
            account_id=envelope.id, balance_cents=1_000, note="Vanessa says $10"
        )

        said = await _said(envelope_balance_describe, async_db_session, payload)
        assert said["Balance"] == "$60.00 → $10.00"
        assert said["Money"] == "moves none: it is what the plan allows, not a transfer"

        await envelope_balance_execute(async_db_session, payload, None)
        await async_db_session.refresh(envelope)
        assert envelope.current_balance == 1_000

    @pytest.mark.asyncio
    async def test_a_balance_it_already_has_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.planning import (
            EnvelopeBalancePayload,
            envelope_balance_describe,
        )

        envelope = await _envelope(async_db_session)
        with pytest.raises(ValueError, match="nothing"):
            await envelope_balance_describe(
                async_db_session,
                EnvelopeBalancePayload(account_id=envelope.id, balance_cents=0),
                None,
            )

    @pytest.mark.asyncio
    async def test_a_new_envelope(self, async_db_session: AsyncSession) -> None:
        from app.services.finance.domains.writes.planning import (
            EnvelopeCreatePayload,
            envelope_create_describe,
            envelope_create_execute,
        )

        payload = EnvelopeCreatePayload(
            name="Groceries", allowance_cents=60_000, cadence="monthly"
        )
        said = await _said(envelope_create_describe, async_db_session, payload)
        assert said["Allowance"] == "$600.00 a month"

        made = await envelope_create_execute(async_db_session, payload, None)
        assert made["account_id"]


def test_every_planning_card_is_registered_and_advertised() -> None:
    from app.services.finance.domains import writes
    from app.services.finance.domains.detection.analyst.prompt_changes import (
        PROPOSING_CHANGES,
    )

    for change_type in (
        "goal.create",
        "goal.update",
        "envelope.create",
        "envelope.update",
    ):
        assert change_type in writes.registered_change_types()
        assert f"`{change_type}`" in PROPOSING_CHANGES
