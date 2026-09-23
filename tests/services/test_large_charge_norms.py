"""A charge is large against what THAT PAYEE usually charges.

The rule compared each charge with its whole account's median. On a card
of streaming subscriptions that median is about fifteen dollars, so the
AMEX Pay Over Time interest - $931, every month for two years - read as
"unusually large" every single month. Its stream had been deleted in a
cleanup, which unlinked it, and the rule's only idea of "recurring" was a
live stream link; this month's charge had not even been given a merchant
yet. Measured on the real ledger 2026-09-22: 27 of 42 open large-charge
alerts were a payee charging what it always charges - the mortgage,
Central Hudson, Claude, ChatGPT, the Acura payment.

So a payee's own history answers first: the same merchant on any
account, or the same description on this account (a description is not
an identity across accounts - every check is "check"). Only a payee with
no history at all falls back to the account's norm.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection import generate_insights
from app.services.finance.models import FinanceInsight, FinanceTransaction
from app.services.finance.service import FinanceService
from tests.services._finance_factories import seed_account, seed_txn

TODAY = date(2026, 9, 20)


async def _flagged(db: AsyncSession) -> set[int]:
    rows = (
        await db.exec(
            select(FinanceInsight).where(
                FinanceInsight.insight_type == "large_transaction"
            )
        )
    ).all()
    return {int(r.related_transaction_id) for r in rows}


async def _card_of_small_charges(svc: FinanceService, name: str = "AMEX") -> int:
    """A card whose median charge is about fifteen dollars."""
    card = await seed_account(
        svc, name=name, account_type="credit_card", classification="liability"
    )
    for n in range(14):
        await seed_txn(
            svc,
            card.id,
            -1_500,
            TODAY - timedelta(days=3 + 5 * n),
            name=f"Streaming {n}",
        )
    return int(card.id)


async def _with_merchant(db: AsyncSession, txn, merchant_id: int | None) -> None:
    row = await db.get(FinanceTransaction, txn.id)
    row.merchant_id = merchant_id
    db.add(row)
    await db.flush()


async def _merchant(db: AsyncSession, name: str) -> int:
    from app.services.finance.models import FinanceMerchant

    merchant = FinanceMerchant(
        name=name, normalized_name=name.lower(), owner_user_id=1, source="user"
    )
    db.add(merchant)
    await db.flush()
    return int(merchant.id)


INTEREST = "Interest Charge on Pay Over Time Purchases XXXX3007"


class TestAPayeeChargingWhatItAlwaysCharges:
    @pytest.mark.asyncio
    async def test_monthly_interest_is_not_large_even_with_no_merchant_yet(
        self, async_db_session: AsyncSession
    ) -> None:
        """The exact case: named in past months, unnamed in this one."""
        db = async_db_session
        svc = FinanceService(db)
        card = await _card_of_small_charges(svc)
        amex = await _merchant(db, "American Express")
        for months, cents in ((1, -91_865), (2, -96_554), (3, -92_582)):
            past = await seed_txn(
                svc, card, cents, TODAY - timedelta(days=30 * months), name=INTEREST
            )
            await _with_merchant(db, past, amex)
        this_month = await seed_txn(
            svc, card, -93_134, TODAY - timedelta(days=4), name=INTEREST
        )

        await generate_insights(db, owner_user_id=1, today=TODAY)

        assert this_month.id not in await _flagged(db)

    @pytest.mark.asyncio
    async def test_a_bill_that_moved_cards_brings_its_history_by_merchant(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        svc = FinanceService(db)
        old_card = await _card_of_small_charges(svc, name="Old card")
        new_card = await _card_of_small_charges(svc, name="New card")
        att = await _merchant(db, "AT&T")
        for months in (1, 2, 3):
            past = await seed_txn(
                svc,
                old_card,
                -22_919,
                TODAY - timedelta(days=30 * months),
                name="ATT BILL PAYMENT",
            )
            await _with_merchant(db, past, att)
        moved = await seed_txn(
            svc, new_card, -22_919, TODAY - timedelta(days=4), name="AT&T"
        )
        await _with_merchant(db, moved, att)

        await generate_insights(db, owner_user_id=1, today=TODAY)

        assert moved.id not in await _flagged(db)

    @pytest.mark.asyncio
    async def test_a_yearly_renewal_at_last_years_price_is_not_large(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        svc = FinanceService(db)
        card = await _card_of_small_charges(svc)
        await seed_txn(
            svc,
            card,
            -32_500,
            TODAY - timedelta(days=365),
            name="RENEWAL MEMBERSHIP FEE XXXX3007",
        )
        renewal = await seed_txn(
            svc,
            card,
            -32_500,
            TODAY - timedelta(days=4),
            name="RENEWAL MEMBERSHIP FEE XXXX3007",
        )

        await generate_insights(db, owner_user_id=1, today=TODAY)

        assert renewal.id not in await _flagged(db)


class TestWhatIsStillUnusual:
    @pytest.mark.asyncio
    async def test_a_payee_charging_far_more_than_usual_is_flagged(
        self, async_db_session: AsyncSession
    ) -> None:
        """Honda, usually $42, then $1,734.67: large for Honda."""
        db = async_db_session
        svc = FinanceService(db)
        card = await _card_of_small_charges(svc)
        await seed_txn(svc, card, -4_200, TODAY - timedelta(days=60), name="Honda")
        big = await seed_txn(
            svc, card, -173_467, TODAY - timedelta(days=4), name="Honda"
        )

        await generate_insights(db, owner_user_id=1, today=TODAY)

        assert big.id in await _flagged(db)

    @pytest.mark.asyncio
    async def test_a_payee_with_no_history_still_meets_the_accounts_norm(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        svc = FinanceService(db)
        card = await _card_of_small_charges(svc)
        first = await seed_txn(
            svc, card, -27_498, TODAY - timedelta(days=4), name="RODIFFYMOISSANITE"
        )

        await generate_insights(db, owner_user_id=1, today=TODAY)

        assert first.id in await _flagged(db)

    @pytest.mark.asyncio
    async def test_a_description_is_not_an_identity_across_accounts(
        self, async_db_session: AsyncSession
    ) -> None:
        """Every check reads "check". Big checks on one account must not
        make a big check on another look normal, nor small checks there
        make them look huge - a description counts on its own account."""
        db = async_db_session
        svc = FinanceService(db)
        checking = await _card_of_small_charges(svc, name="Checking")
        other = await _card_of_small_charges(svc, name="Other checking")
        for n in range(3):
            await seed_txn(
                svc,
                other,
                -150_000,
                TODAY - timedelta(days=30 * (n + 1)),
                name=f"CHECK # {800 + n} {800 + n}",
            )
        big_check = await seed_txn(
            svc, checking, -150_000, TODAY - timedelta(days=4), name="CHECK # 2058 2058"
        )

        await generate_insights(db, owner_user_id=1, today=TODAY)

        assert big_check.id in await _flagged(db)


class TestTheOldFalseAlarmsGoAway:
    """27 open alerts on the real ledger were a payee charging its usual.
    Judging new charges fairly is half the fix; the rule also takes back
    the ones it raised under the old norm - but never one the person has
    already resolved, which is a record now."""

    @pytest.mark.asyncio
    async def test_an_alert_the_payee_norm_would_not_raise_is_retracted(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.detection.insights.rules import (
            create_insight_if_new,
        )

        db = async_db_session
        svc = FinanceService(db)
        card = await _card_of_small_charges(svc)
        for months in (1, 2, 3):
            await seed_txn(
                svc, card, -92_000, TODAY - timedelta(days=30 * months), name=INTEREST
            )
        charge = await seed_txn(
            svc, card, -93_134, TODAY - timedelta(days=4), name=INTEREST
        )
        # Raised under the old account-median norm.
        await create_insight_if_new(
            db,
            owner_user_id=1,
            insight_type="large_transaction",
            dedup_key=f"large_txn:{charge.id}",
            severity="critical",
            title="Large charge: $931.34",
            body="old",
            related_transaction_id=charge.id,
        )

        await generate_insights(db, owner_user_id=1, today=TODAY)

        assert charge.id not in await _flagged(db)

    @pytest.mark.asyncio
    async def test_a_resolved_one_is_kept(self, async_db_session: AsyncSession) -> None:
        from app.services.finance.domains.detection.insights.rules import (
            create_insight_if_new,
        )
        from app.services.finance.domains.planning.insights import resolve_insight

        db = async_db_session
        svc = FinanceService(db)
        card = await _card_of_small_charges(svc)
        for months in (1, 2, 3):
            await seed_txn(
                svc, card, -92_000, TODAY - timedelta(days=30 * months), name=INTEREST
            )
        charge = await seed_txn(
            svc, card, -93_134, TODAY - timedelta(days=4), name=INTEREST
        )
        alert = await create_insight_if_new(
            db,
            owner_user_id=1,
            insight_type="large_transaction",
            dedup_key=f"large_txn:{charge.id}",
            severity="critical",
            title="Large charge: $931.34",
            body="old",
            related_transaction_id=charge.id,
        )
        await resolve_insight(
            db,
            int(alert.id),
            state="legitimate",
            note="Monthly interest.",
            owner_user_id=1,
        )

        await generate_insights(db, owner_user_id=1, today=TODAY)

        assert charge.id in await _flagged(db)


class TestTheFloorStillHolds:
    @pytest.mark.asyncio
    async def test_double_a_small_usual_is_still_small(
        self, async_db_session: AsyncSession
    ) -> None:
        """Found measuring on the real ledger: with no floor on the payee
        branch, a $30 Starbucks against a usual $15 became a "large
        charge", and 22 of them appeared in one run. Large means large."""
        db = async_db_session
        svc = FinanceService(db)
        card = await _card_of_small_charges(svc)
        for months in (1, 2, 3):
            await seed_txn(
                svc, card, -1_500, TODAY - timedelta(days=30 * months), name="Starbucks"
            )
        coffee = await seed_txn(
            svc, card, -3_000, TODAY - timedelta(days=4), name="Starbucks"
        )

        await generate_insights(db, owner_user_id=1, today=TODAY)

        assert coffee.id not in await _flagged(db)


class TestAPendingDecisionWins:
    """Found live: the fixed rule withdrew insight 1058 while a card to
    resolve it sat pending, and approving it answered "Insight 1058 not
    found." A person mid-decision on an alert owns it until they decide."""

    @pytest.mark.asyncio
    async def test_an_alert_with_a_pending_resolution_card_is_not_withdrawn(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.detection.insights.rules import (
            create_insight_if_new,
        )
        from app.services.finance.domains.writes.queue import approve, propose

        db = async_db_session
        svc = FinanceService(db)
        card = await _card_of_small_charges(svc)
        for months in (1, 2, 3):
            await seed_txn(
                svc, card, -92_000, TODAY - timedelta(days=30 * months), name=INTEREST
            )
        charge = await seed_txn(
            svc, card, -93_134, TODAY - timedelta(days=4), name=INTEREST
        )
        alert = await create_insight_if_new(
            db,
            owner_user_id=1,
            insight_type="large_transaction",
            dedup_key=f"large_txn:{charge.id}",
            severity="critical",
            title="Large charge: $931.34",
            body="old",
            related_transaction_id=charge.id,
        )
        pending = await propose(
            db,
            "insight.resolve",
            {
                "insight_id": alert.id,
                "state": "legitimate",
                "note": "Monthly interest.",
            },
            owner_user_id=1,
        )

        await generate_insights(db, owner_user_id=1, today=TODAY)
        assert charge.id in await _flagged(db)

        await approve(db, int(pending.id), owner_user_id=1)
        await db.refresh(alert)
        assert alert.resolution == "legitimate"
