"""An envelope that follows a tag: what the household buys for her, she
pays for from her envelope.

Vanessa's allowance is virtual, but the Roblox codes and the rest are
real charges on real cards. Tagging one "Vanessa" spends it from her
envelope; untagging gives it back (#240). The envelope settles against
the ledger, so re-reading, re-importing or re-tagging never counts a
charge twice.
"""

from datetime import timedelta
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.utils import current_date
from tests.services._finance_factories import seed_account, seed_txn

# Finance's one clock, not the machine's: in UTC it can already be tomorrow.
TODAY = current_date()


async def _setup(db: AsyncSession) -> dict[str, Any]:
    from app.services.finance.domains.planning.envelope_tags import follow_tag
    from app.services.finance.service import FinanceService

    svc = FinanceService(db)
    card = await seed_account(svc, name="Household card", account_type="credit_card")
    envelope = await svc.create_envelope(
        owner_user_id=1, name="Vanessa's", monthly_credit=1_000, cadence="weekly"
    )
    await svc.credit_envelope(envelope.id, amount=6_000, owner_user_id=1)
    await follow_tag(db, envelope.id, "Vanessa", owner_user_id=1, since=TODAY)
    return {"svc": svc, "card": card, "envelope": envelope}


async def _balance(db: AsyncSession, envelope: Any) -> int:
    await db.refresh(envelope)
    return envelope.current_balance


class TestSpendingFromATag:
    @pytest.mark.asyncio
    async def test_tagging_a_charge_spends_it_from_the_envelope(
        self, async_db_session: AsyncSession
    ) -> None:
        made = await _setup(async_db_session)
        roblox = await seed_txn(
            made["svc"], made["card"].id, -540, TODAY, name="ROBLOX"
        )

        await made["svc"].tag_transactions([roblox.id], "Vanessa", owner_user_id=1)

        assert await _balance(async_db_session, made["envelope"]) == 6_000 - 540

    @pytest.mark.asyncio
    async def test_untagging_gives_it_back(
        self, async_db_session: AsyncSession
    ) -> None:
        made = await _setup(async_db_session)
        roblox = await seed_txn(
            made["svc"], made["card"].id, -540, TODAY, name="ROBLOX"
        )
        tag = await made["svc"].tag_transactions(
            [roblox.id], "Vanessa", owner_user_id=1
        )

        await made["svc"].untag_transactions([roblox.id], tag.id, owner_user_id=1)

        assert await _balance(async_db_session, made["envelope"]) == 6_000

    @pytest.mark.asyncio
    async def test_settling_twice_counts_once(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.planning.envelope_tags import settle

        made = await _setup(async_db_session)
        roblox = await seed_txn(
            made["svc"], made["card"].id, -540, TODAY, name="ROBLOX"
        )
        await made["svc"].tag_transactions([roblox.id], "Vanessa", owner_user_id=1)

        await settle(async_db_session, owner_user_id=1)
        await settle(async_db_session, owner_user_id=1)

        assert await _balance(async_db_session, made["envelope"]) == 6_000 - 540

    @pytest.mark.asyncio
    async def test_what_was_tagged_before_it_followed_the_tag_is_history(
        self, async_db_session: AsyncSession
    ) -> None:
        """The real "Vanessa" tag has 42 charges on it back to 2019; an
        envelope pointed at it today must not spend all of them."""
        from app.services.finance.domains.planning.envelope_tags import settle

        made = await _setup(async_db_session)
        old = await seed_txn(
            made["svc"],
            made["card"].id,
            -1_080,
            TODAY - timedelta(days=400),
            name="ROBLOX",
        )
        await made["svc"].tag_transactions([old.id], "Vanessa", owner_user_id=1)
        await settle(async_db_session, owner_user_id=1)

        assert await _balance(async_db_session, made["envelope"]) == 6_000

    @pytest.mark.asyncio
    async def test_a_tagged_transfer_is_not_spending(
        self, async_db_session: AsyncSession
    ) -> None:
        made = await _setup(async_db_session)
        sent = await seed_txn(made["svc"], made["card"].id, -1_000, TODAY, name="Venmo")
        sent.is_transfer = True
        async_db_session.add(sent)
        await async_db_session.flush()

        await made["svc"].tag_transactions([sent.id], "Vanessa", owner_user_id=1)

        assert await _balance(async_db_session, made["envelope"]) == 6_000


class TestTheCardCanPointItAtATag:
    @pytest.mark.asyncio
    async def test_envelope_update_names_the_tag(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.planning.envelopes import envelope_metadata
        from app.services.finance.domains.writes.planning import (
            EnvelopeUpdatePayload,
            envelope_update_describe,
            envelope_update_execute,
        )
        from app.services.finance.service import FinanceService

        envelope = await FinanceService(async_db_session).create_envelope(
            owner_user_id=1, name="Vanessa's", monthly_credit=1_000, cadence="weekly"
        )
        payload = EnvelopeUpdatePayload(account_id=envelope.id, tag="Vanessa")

        said = {
            row.label: row.value
            for row in await envelope_update_describe(async_db_session, payload, None)
        }
        assert (
            said["Pays for"] == f"- → what is tagged Vanessa, from {TODAY.isoformat()}"
        )

        await envelope_update_execute(async_db_session, payload, None)
        await async_db_session.refresh(envelope)
        meta = envelope_metadata(envelope.metadata_)
        assert meta is not None and meta.tag_id is not None
        assert meta.tag_since == TODAY


class TestCountingFrom:
    @pytest.mark.asyncio
    async def test_moving_the_start_back_counts_what_it_now_covers(
        self, async_db_session: AsyncSession
    ) -> None:
        """Live: two August Dark Side Records charges were tagged Vanessa
        and her envelope did not move, because counting starts the day the
        tag is set (2026-09-23). The start is the person's to choose."""
        from app.services.finance.domains.planning.envelope_tags import retag

        made = await _setup(async_db_session)
        older = await seed_txn(
            made["svc"],
            made["card"].id,
            -540,
            TODAY - timedelta(days=30),
            name="ROBLOX",
        )
        await made["svc"].tag_transactions([older.id], "Vanessa", owner_user_id=1)
        assert await _balance(async_db_session, made["envelope"]) == 6_000

        await retag(
            async_db_session,
            made["envelope"].id,
            "Vanessa",
            owner_user_id=1,
            since=TODAY - timedelta(days=60),
        )

        assert await _balance(async_db_session, made["envelope"]) == 6_000 - 540

    @pytest.mark.asyncio
    async def test_moving_it_forward_gives_back_what_it_no_longer_covers(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.planning.envelope_tags import retag

        made = await _setup(async_db_session)
        older = await seed_txn(
            made["svc"],
            made["card"].id,
            -540,
            TODAY - timedelta(days=30),
            name="ROBLOX",
        )
        await made["svc"].tag_transactions([older.id], "Vanessa", owner_user_id=1)
        await retag(
            async_db_session,
            made["envelope"].id,
            "Vanessa",
            owner_user_id=1,
            since=TODAY - timedelta(days=60),
        )

        await retag(
            async_db_session,
            made["envelope"].id,
            "Vanessa",
            owner_user_id=1,
            since=TODAY,
        )

        assert await _balance(async_db_session, made["envelope"]) == 6_000

    @pytest.mark.asyncio
    async def test_resaving_without_a_start_keeps_the_start(
        self, async_db_session: AsyncSession
    ) -> None:
        """Saving the dialog again must not quietly move counting to today."""
        from app.services.finance.domains.planning.envelope_tags import retag
        from app.services.finance.domains.planning.envelopes import envelope_metadata

        made = await _setup(async_db_session)
        start = TODAY - timedelta(days=45)
        await retag(
            async_db_session,
            made["envelope"].id,
            "Vanessa",
            owner_user_id=1,
            since=start,
        )

        changed = await retag(
            async_db_session, made["envelope"].id, "Vanessa", owner_user_id=1
        )

        await async_db_session.refresh(made["envelope"])
        assert changed is False
        assert envelope_metadata(made["envelope"].metadata_).tag_since == start

    @pytest.mark.asyncio
    async def test_the_card_moves_the_start_and_says_so(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.planning import (
            EnvelopeUpdatePayload,
            envelope_update_describe,
            envelope_update_execute,
        )

        made = await _setup(async_db_session)
        start = TODAY - timedelta(days=45)
        payload = EnvelopeUpdatePayload(account_id=made["envelope"].id, tag_since=start)

        said = {
            row.label: row.value
            for row in await envelope_update_describe(async_db_session, payload, None)
        }
        assert said["Counting from"] == f"{TODAY.isoformat()} → {start.isoformat()}"

        await envelope_update_execute(async_db_session, payload, None)
        from app.services.finance.domains.planning.envelopes import envelope_metadata

        await async_db_session.refresh(made["envelope"])
        assert envelope_metadata(made["envelope"].metadata_).tag_since == start
