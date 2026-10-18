"""One bill, one stream (milestone: #337-#341).

Each rule here came off the household's own ledger:

- two memberships at one car wash, $43.20 and $32.38 a day apart each
  month, were one confirmed bill and a second one detection dropped as
  "that bill again" (#337);
- a paycheck on the 15th and the last day read as every two weeks, or
  not at all, and its next date drifted off both days (#338);
- a $43.24 yearly renewal charged twice, a year apart, was never found:
  three charges took three years (#339).
"""

import calendar
from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection import detect_recurring
from app.services.finance.domains.planning.recurring.schedule import occurrences
from app.services.finance.pay_days import next_pay_day, pay_days_of
from app.services.finance.service import FinanceService
from app.services.finance.utils import stream_step
from tests.services._finance_factories import live_streams
from tests.services._finance_factories import seed_account as _account
from tests.services._finance_factories import seed_spend_series as _spend

TODAY = date(2026, 10, 1)


class TestTwoBillsFromOnePayee:
    @pytest.mark.asyncio
    async def test_a_second_membership_beside_a_confirmed_one_is_its_own_bill(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account = await _account(svc)
        await _spend(
            svc,
            account.id,
            [date(2026, m, 21) for m in range(3, 10)],
            cents=-4320,
            name="FOAM & WASH",
        )
        await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)
        (first,) = await live_streams(async_db_session)
        await svc.confirm_recurring(first.id, owner_user_id=1)

        await _spend(
            svc,
            account.id,
            [date(2026, m, 20) for m in range(4, 10)],
            cents=-3238,
            name="FOAM & WASH",
        )
        await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)

        amounts = sorted(s.amount for s in await live_streams(async_db_session))
        assert amounts == [3238, 4320]

    @pytest.mark.asyncio
    async def test_a_bills_old_price_is_still_that_bill(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The other side: a price that ended where the new one began is
        the bill's own history, never a second bill."""
        account = await _account(svc)
        await _spend(
            svc,
            account.id,
            [date(2026, m, 21) for m in range(7, 10)],
            cents=-4320,
            name="FOAM & WASH",
        )
        await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)
        (bill,) = await live_streams(async_db_session)
        await svc.confirm_recurring(bill.id, owner_user_id=1)

        await _spend(
            svc,
            account.id,
            [date(2026, m, 21) for m in range(3, 7)],
            cents=-3238,
            name="FOAM & WASH",
        )
        await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)

        assert [s.id for s in await live_streams(async_db_session)] == [bill.id]


class TestTwiceAMonth:
    def test_the_15th_and_the_last_day_are_its_two_days(self) -> None:
        # Paid on the 15th and the last day, Aug 15 (a Saturday) on Monday.
        paid = [
            date(2026, 6, 15),
            date(2026, 6, 30),
            date(2026, 7, 15),
            date(2026, 7, 31),
            date(2026, 8, 17),
            date(2026, 8, 31),
            date(2026, 9, 15),
            date(2026, 9, 30),
        ]

        assert pay_days_of(paid) == (15, 31)

    def test_every_two_weeks_drifts_and_has_no_two_days(self) -> None:
        start = date(2026, 1, 2)
        paid = [date.fromordinal(start.toordinal() + 14 * n) for n in range(12)]

        assert pay_days_of(paid) is None

    def test_the_next_date_lands_on_its_days_month_after_month(self) -> None:
        days = (15, 31)

        assert next_pay_day(date(2026, 9, 30), days) == date(2026, 10, 15)
        assert next_pay_day(date(2026, 10, 15), days) == date(2026, 10, 31)
        assert next_pay_day(date(2027, 1, 31), days) == date(2027, 2, 15)
        assert next_pay_day(date(2027, 2, 15), days) == date(2027, 2, 28)
        # Paid early on a Friday is still that payday: the next is the other.
        assert next_pay_day(date(2026, 10, 14), days) == date(2026, 10, 31)

    @pytest.mark.asyncio
    async def test_a_paycheck_on_two_fixed_days_is_one_twice_monthly_stream(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account = await _account(svc)
        days = [
            date(2026, m, d)
            for m in range(4, 10)
            for d in (15, calendar.monthrange(2026, m)[1])
        ]
        await _spend(svc, account.id, days, cents=500_000, name="ACME PAYROLL")

        await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)

        (paycheck,) = await live_streams(async_db_session)
        assert paycheck.frequency == "semi_monthly"
        assert paycheck.pay_days == [15, 31]
        assert paycheck.next_expected_date == date(2026, 10, 15)
        ahead = [
            o.due_on
            for o in occurrences(paycheck, today=TODAY, through=date(2026, 12, 31))
        ]
        assert ahead == [
            date(2026, 10, 15),
            date(2026, 10, 31),
            date(2026, 11, 15),
            date(2026, 11, 30),
            date(2026, 12, 15),
            date(2026, 12, 31),
        ]

    @pytest.mark.asyncio
    async def test_every_two_weeks_stays_every_two_weeks(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account = await _account(svc)
        start = date(2026, 4, 3)
        days = [date.fromordinal(start.toordinal() + 14 * n) for n in range(12)]
        await _spend(svc, account.id, days, cents=300_000, name="ACME PAYROLL")

        await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)

        (paycheck,) = await live_streams(async_db_session)
        assert paycheck.frequency == "biweekly"
        assert paycheck.pay_days == []

    def test_a_bill_set_twice_monthly_by_hand_steps_on_its_own_dates(self) -> None:
        """Declared before it kept its days: its last and next dates
        name them."""

        class Paycheck:
            frequency = "semi_monthly"
            pay_days: list[int] = []
            last_date = date(2026, 9, 15)
            next_expected_date = date(2026, 9, 30)

        step = stream_step(Paycheck)
        assert step is not None
        assert step(date(2026, 9, 30)) == date(2026, 10, 15)
        assert step(date(2026, 10, 15)) == date(2026, 10, 31)


class TestAYearlyChargeFromTwo:
    @pytest.mark.asyncio
    async def test_two_charges_a_year_apart_propose_a_yearly_bill(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account = await _account(svc)
        await _spend(
            svc,
            account.id,
            [date(2025, 4, 11), date(2026, 4, 13)],
            cents=-4324,
            name="LG ELECTRONICS",
        )

        await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)

        (renewal,) = await live_streams(async_db_session)
        assert renewal.frequency == "annually"
        assert renewal.next_expected_date is not None
        assert renewal.next_expected_date.year == 2027

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("name", "amounts"),
        [
            # Cash a year from the last is chance, not a renewal.
            ("ATM WITHDRAWAL 09 MAIN", (-48_000, -52_000)),
            # Two prices that far apart are two purchases.
            ("HARDWARE STORE", (-4_000, -9_000)),
        ],
    )
    async def test_cash_or_two_prices_is_not_a_renewal(
        self,
        svc: FinanceService,
        async_db_session: AsyncSession,
        name: str,
        amounts: tuple[int, int],
    ) -> None:
        account = await _account(svc)
        for day, cents in zip((date(2025, 9, 18), date(2026, 9, 15)), amounts):
            await _spend(svc, account.id, [day], cents=cents, name=name)

        await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)

        assert await live_streams(async_db_session) == []


class TestACardPaymentAtItsStatement:
    """A card paid in full is expected at its latest statement, not at an
    average of statements already paid (#341)."""

    async def _card_paid_monthly(
        self, svc: FinanceService, db: AsyncSession, amounts: list[int]
    ):
        from app.services.finance.domains.detection import detect_transfers

        checking = await _account(svc)
        card = await svc.create_manual_account(
            name="Amex",
            account_type="credit_card",
            classification="liability",
            owner_user_id=1,
        )
        card.mask = "3007"
        db.add(card)
        for month, cents in zip(range(3, 3 + len(amounts)), amounts):
            for account, signed, name in (
                (checking, -cents, "AMERICAN EXPRESS ACH PMT"),
                (card, cents, "AUTOPAY PAYMENT - THANK YOU"),
            ):
                await svc.create_transaction(
                    account_id=account.id,
                    amount=signed,
                    txn_date=date(2026, month, 13),
                    owner_user_id=1,
                    name=name,
                )
        await detect_transfers(db, owner_user_id=1, today=TODAY, lookback_days=0)
        await detect_recurring(db, owner_user_id=1, today=TODAY)
        (payment,) = [s for s in await live_streams(db) if s.account_id == checking.id]
        return card, payment

    @pytest.mark.asyncio
    async def test_paid_in_full_it_expects_the_statement_on_its_due_date(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.planning.recurring.statements import (
            record_statement,
        )

        card, payment = await self._card_paid_monthly(
            svc, async_db_session, [120_400, 187_001, 243_861, 96_400, 201_250]
        )
        assert payment.amount_is_variable

        assert await record_statement(
            async_db_session,
            card,
            balance=785_727,
            issued=date(2026, 9, 7),
            due=date(2026, 10, 3),
        )

        assert (payment.amount, payment.next_expected_date) == (
            785_727,
            date(2026, 10, 3),
        )

    @pytest.mark.asyncio
    async def test_a_fixed_payment_keeps_its_figure(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """$250 a month toward a $7,857 balance: the statement is not what
        leaves the account."""
        from app.services.finance.domains.planning.recurring.statements import (
            record_statement,
        )

        card, payment = await self._card_paid_monthly(
            svc, async_db_session, [25_000] * 5
        )

        await record_statement(
            async_db_session,
            card,
            balance=785_727,
            issued=date(2026, 9, 7),
            due=date(2026, 10, 3),
        )

        assert payment.amount == 25_000

    @pytest.mark.asyncio
    async def test_an_older_statement_filed_late_changes_nothing(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.planning.recurring.statements import (
            record_statement,
        )

        card, payment = await self._card_paid_monthly(
            svc, async_db_session, [120_400, 187_001, 243_861, 96_400, 201_250]
        )
        await record_statement(
            async_db_session, card, balance=785_727, issued=date(2026, 9, 7)
        )

        assert not await record_statement(
            async_db_session, card, balance=111_111, issued=date(2026, 7, 7)
        )
        assert payment.amount == 785_727

    @pytest.mark.asyncio
    async def test_a_filed_statement_on_one_of_our_cards_is_read(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.statements import (
            record_card_statement,
        )
        from app.services.documents.models import Document, DocumentPage
        from app.services.finance.domains.ledger.queries.accounts import (
            liability_details_by_account,
        )

        card, payment = await self._card_paid_monthly(
            svc, async_db_session, [120_400, 187_001, 243_861, 96_400, 201_250]
        )
        document = Document(
            title="statement.pdf",
            filename="statement.pdf",
            storage_key="testcase/card-statement.pdf",
            media_type="application/pdf",
            content_hash="testcase-card-statement",
            size_bytes=9,
        )
        async_db_session.add(document)
        await async_db_session.flush()
        async_db_session.add(
            DocumentPage(
                document_id=document.id,
                page_number=1,
                status="read",
                method="text",
                text="\n".join(
                    [
                        "Account ending 3007",
                        "New balance as of 09/07/26: $7,857.27",
                        "Minimum payment due: $224.57",
                        "Payment due date: 10/03/26",
                    ]
                ),
            )
        )
        await async_db_session.flush()

        assert await record_card_statement(async_db_session, document.id)

        assert card.id is not None
        kept = (await liability_details_by_account(async_db_session, [card.id]))[
            card.id
        ]
        assert (
            kept.last_statement_balance,
            kept.last_statement_issue_date,
            kept.next_payment_due_date,
            kept.minimum_payment_amount,
        ) == (785_727, date(2026, 9, 7), date(2026, 10, 3), 22_457)
        assert payment.next_expected_date == date(2026, 10, 3)


class TestANewBillIsAnnouncedByRescan:
    @pytest.mark.asyncio
    async def test_a_bill_rescan_finds_is_in_attention_at_once(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """#340: the rules a sync runs after detection, so Rescan's find is
        news now, not after the next import."""
        from sqlmodel import select

        from app.components.backend.api.finance.recurring import rescan_recurring
        from app.services.finance.models import FinanceInsight
        from app.services.finance.utils import current_date

        account = await _account(svc)
        today = current_date()
        await _spend(
            svc,
            account.id,
            [date.fromordinal(today.toordinal() - 30 * n) for n in range(1, 6)],
            cents=-1_599,
            name="STREAMING CO",
        )

        await rescan_recurring(service=svc, owner_user_id=1)

        news = (
            await async_db_session.exec(
                select(FinanceInsight).where(
                    FinanceInsight.insight_type == "new_recurring"
                )
            )
        ).all()
        assert [n.title for n in news] == ["New bill: STREAMING CO"]


@pytest.mark.asyncio
async def test_a_bills_payment_arriving_twice_is_not_a_second_bill(
    svc: FinanceService, async_db_session: AsyncSession
) -> None:
    """#337's other side: a copy of each payment a day off (a file row a
    sync's dedup missed) sits beside the bill at its own price."""
    account = await _account(svc)
    await _spend(
        svc,
        account.id,
        [date(2026, m, 21) for m in range(3, 10)],
        cents=-4320,
        name="FOAM & WASH",
    )
    await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)
    (bill,) = await live_streams(async_db_session)
    await svc.confirm_recurring(bill.id, owner_user_id=1)

    await _spend(
        svc,
        account.id,
        [date(2026, m, 22) for m in range(4, 10)],
        cents=-4320,
        name="FOAM & WASH CAR WASH",
    )
    await detect_recurring(async_db_session, owner_user_id=1, today=TODAY)

    assert [s.id for s in await live_streams(async_db_session)] == [bill.id]
