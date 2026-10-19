"""A loan payment splits into principal, interest and escrow (#318).

Only principal is money moved: interest and escrow are what the house
costs. A payment that paired as a transfer left spending whole, so a
mortgage read as $0 of housing; a payment the loan's own register shows
only the principal of never paired at all, and counted whole. Now the
cash side splits - principal as a transfer, the rest as spending - and
what is estimated from the balance and rate says so.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection import detect_transfers
from app.services.finance.models import (
    FinanceCategory,
    FinanceLiabilityDetail,
    FinanceTransaction,
    FinanceTransactionSplit,
    FinanceTransfer,
)
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.services._finance_factories import seed_account, seed_txn


async def _loan(
    svc: FinanceService,
    db: AsyncSession,
    *,
    owed: int,
    rate_bps: int | None,
    payment: int | None = None,
    escrow: int | None = None,
) -> int:
    loan = await seed_account(
        svc, "Mortgage", "loan", "liability", current_balance=-owed
    )
    assert loan.id is not None
    db.add(
        FinanceLiabilityDetail(
            account_id=loan.id,
            interest_rate_bps=rate_bps,
            minimum_payment_amount=payment,
            escrow_amount=escrow,
        )
    )
    await db.flush()
    return loan.id


async def _lines(db: AsyncSession, parent_id: int) -> list[tuple[int, str, str]]:
    """(amount, category classification, memo) per line, largest first."""
    rows = (
        await db.exec(
            select(
                FinanceTransactionSplit.amount,
                FinanceCategory.classification,
                FinanceTransactionSplit.memo,
            )
            .join(
                FinanceCategory,
                FinanceCategory.id == FinanceTransactionSplit.category_id,
            )
            .where(FinanceTransactionSplit.parent_transaction_id == parent_id)
            .order_by(FinanceTransactionSplit.amount)
        )
    ).all()
    return [(amount, kind, memo or "") for amount, kind, memo in rows]


class TestTheLoanShowsOnlyThePrincipal:
    """The household's shape: $2,553.23 leaves checking, and the loan's
    register shows the $500.14 it came down by, two days later."""

    @pytest.mark.asyncio
    async def test_the_payment_splits_on_the_banks_principal(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        loan = await _loan(
            svc, async_db_session, owed=17_545_790, rate_bps=413, payment=255_323
        )
        checking = await seed_account(svc)
        housing = await svc.get_or_create_category_from_hint("Home:Mortgage & Rent")
        day = current_date() - timedelta(days=5)
        payment = await seed_txn(
            svc, checking.id, -255_323, day, name="Citizens", category_id=housing.id
        )
        principal = await seed_txn(
            svc,
            loan,
            50_014,
            day + timedelta(days=2),
            name="Citizens",
            category_id=housing.id,
        )

        await detect_transfers(async_db_session, owner_user_id=1, today=day)

        assert payment.id is not None
        # Principal is the bank's own figure; interest is estimated from
        # the balance and rate, and escrow is what is left.
        assert await _lines(async_db_session, payment.id) == [
            (-144_922, "expense", "Escrow and fees (estimated)"),
            (-60_387, "expense", "Interest (estimated)"),
            (-50_014, "transfer", "Principal"),
        ]
        assert (principal.is_transfer, principal.excluded_from_reports) == (True, True)
        assert payment.transfer_pair_transaction_id == principal.id
        flow = await svc.cash_flow(owner_user_id=1, start=day)
        assert (flow.spending, flow.income) == (205_309, 0)  # $2,553.23 - $500.14

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # detection twice: the second splits nothing
    async def test_without_a_rate_the_rest_is_one_line_and_not_a_guess(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        loan = await _loan(
            svc, async_db_session, owed=10_000_000, rate_bps=None, payment=150_000
        )
        checking = await seed_account(svc)
        day = current_date().replace(day=1)
        payment = await seed_txn(svc, checking.id, -150_000, day, name="Loan")
        await seed_txn(svc, loan, 90_000, day, name="Loan")

        await detect_transfers(async_db_session, owner_user_id=1, today=day)
        again = await detect_transfers(async_db_session, owner_user_id=1, today=day)

        assert payment.id is not None
        assert await _lines(async_db_session, payment.id) == [
            (-90_000, "transfer", "Principal"),
            (-60_000, "expense", "Interest and escrow"),
        ]
        assert again.loan_split == 0


class TestTheLoanShowsTheWholePayment:
    @pytest.mark.asyncio
    async def test_a_paired_payment_comes_back_as_its_interest_and_escrow(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        loan = await _loan(
            svc,
            async_db_session,
            owed=10_000_000,
            rate_bps=600,
            payment=170_000,
            escrow=20_000,
        )
        checking = await seed_account(svc)
        day = current_date().replace(day=1)
        payment = await seed_txn(svc, checking.id, -170_000, day, name="LOAN PAYMENT")
        await seed_txn(svc, loan, 170_000, day, name="LOAN PAYMENT")

        await detect_transfers(async_db_session, owner_user_id=1, today=day)

        assert payment.id is not None
        assert await _lines(async_db_session, payment.id) == [
            (-100_000, "transfer", "Principal (estimated)"),
            (-50_000, "expense", "Interest (estimated)"),
            (-20_000, "expense", "Escrow"),
        ]
        row = await async_db_session.get(FinanceTransaction, payment.id)
        assert row is not None and row.is_transfer is False
        flow = await svc.cash_flow(owner_user_id=1, start=day)
        assert flow.spending == 70_000


@pytest.mark.asyncio
async def test_a_credit_on_a_loan_is_principal_never_a_return(
    svc: FinanceService, async_db_session: AsyncSession
) -> None:
    """Paid from somewhere nothing pairs it with, filed under a spending
    category the way a feed files it: still the balance coming down, so
    it neither brings spending down nor reads as income."""
    loan = await _loan(svc, async_db_session, owed=600_000, rate_bps=None)
    checking = await seed_account(svc)
    adjustment = await svc.get_or_create_category_from_hint("Adjustment")
    day = current_date() - timedelta(days=3)
    await seed_txn(svc, checking.id, -9_000, day, category_id=adjustment.id)
    await seed_txn(svc, loan, 56_682, day, category_id=adjustment.id)

    flow = await svc.cash_flow(owner_user_id=1, start=day)

    assert (flow.income, flow.spending, flow.unfiled) == (0, 9_000, 0)
    assert await svc.spending_by_category(owner_user_id=1, days=10) == [
        ("Adjustment", 9_000)
    ]


class TestWhatTheReviewFound:
    async def _citizens(
        self, svc: FinanceService, db: AsyncSession, *, filed: str
    ) -> tuple[int, int, object]:
        """The household's mortgage shape, the payment filed ``filed``."""
        loan = await _loan(svc, db, owed=17_545_790, rate_bps=413, payment=255_323)
        checking = await seed_account(svc)
        category = await svc.get_or_create_category_from_hint(filed)
        day = current_date() - timedelta(days=5)
        payment = await seed_txn(
            svc, checking.id, -255_323, day, name="Citizens", category_id=category.id
        )
        credit = await seed_txn(
            svc, loan, 50_014, day + timedelta(days=2), name="Citizens"
        )
        assert payment.id is not None and credit.id is not None
        return payment.id, credit.id, day

    @pytest.mark.asyncio
    async def test_filed_as_a_transfer_its_lines_still_count(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The category pass flags it out of every total first; split, it
        has to count again through its lines."""
        _payment, _credit, day = await self._citizens(
            svc, async_db_session, filed="Transfer:Mortgage"
        )

        await detect_transfers(async_db_session, owner_user_id=1, today=day)

        flow = await svc.cash_flow(owner_user_id=1, start=day)
        assert flow.spending == 205_309

    @pytest.mark.asyncio
    async def test_a_payee_list_counts_the_lines_not_the_whole_payment(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.ledger.queries import top_payees_over_window

        _payment, _credit, day = await self._citizens(
            svc, async_db_session, filed="Home:Mortgage & Rent"
        )
        await detect_transfers(async_db_session, owner_user_id=1, today=day)

        payees = await top_payees_over_window(async_db_session, owner_user_id=1)

        assert payees == [("Citizens", 205_309, 1)]

    @pytest.mark.asyncio
    async def test_not_a_transfer_puts_the_payment_back_whole(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.detection.transfers.answers import (
            break_pair,
        )

        payment, _credit, day = await self._citizens(
            svc, async_db_session, filed="Home:Mortgage & Rent"
        )
        await detect_transfers(async_db_session, owner_user_id=1, today=day)

        await break_pair(async_db_session, payment, owner_user_id=1)

        assert await _lines(async_db_session, payment) == []
        flow = await svc.cash_flow(owner_user_id=1, start=day)
        assert flow.spending == 255_323

    @pytest.mark.asyncio
    async def test_with_no_balance_known_it_does_not_guess_zero_interest(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        loan = await _loan(svc, async_db_session, owed=0, rate_bps=600, payment=170_000)
        checking = await seed_account(svc)
        day = current_date() - timedelta(days=1)
        payment = await seed_txn(svc, checking.id, -170_000, day, name="LOAN PAYMENT")
        await seed_txn(svc, loan, 170_000, day, name="LOAN PAYMENT")

        await detect_transfers(async_db_session, owner_user_id=1, today=day)

        assert payment.id is not None
        assert await _lines(async_db_session, payment.id) == []
        assert payment.is_transfer is True


class TestOnlyTheExpectedPaymentCarriesInterest:
    @pytest.mark.asyncio
    async def test_an_extra_payment_to_principal_is_not_charged_a_months_interest(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """Every payment into the loan was split as a month's interest
        and escrow: an extra $5,000 doubled the month's housing."""
        loan = await _loan(
            svc,
            async_db_session,
            owed=10_000_000,
            rate_bps=600,
            payment=170_000,
            escrow=20_000,
        )
        checking = await seed_account(svc)
        first = current_date().replace(day=1)
        await seed_txn(svc, checking.id, -170_000, first, name="LOAN PAYMENT")
        await seed_txn(svc, loan, 170_000, first, name="LOAN PAYMENT")
        extra = await seed_txn(
            svc, checking.id, -500_000, first, name="LOAN PAYMENT PRINCIPAL"
        )
        await seed_txn(svc, loan, 500_000, first, name="LOAN PAYMENT PRINCIPAL")

        await detect_transfers(async_db_session, owner_user_id=1, today=first)

        assert extra.id is not None
        assert await _lines(async_db_session, extra.id) == []
        flow = await svc.cash_flow(owner_user_id=1, start=first)
        assert flow.spending == 70_000  # one month's $500 interest, $200 escrow

    @pytest.mark.asyncio
    async def test_with_no_payment_on_file_nothing_is_split(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        loan = await _loan(svc, async_db_session, owed=10_000_000, rate_bps=600)
        checking = await seed_account(svc)
        day = current_date().replace(day=1)
        payment = await seed_txn(svc, checking.id, -170_000, day, name="LOAN PAYMENT")
        await seed_txn(svc, loan, 170_000, day, name="LOAN PAYMENT")

        await detect_transfers(async_db_session, owner_user_id=1, today=day)

        assert payment.id is not None
        assert await _lines(async_db_session, payment.id) == []


@pytest.mark.asyncio
async def test_a_car_loans_interest_is_not_housing(
    svc: FinanceService, async_db_session: AsyncSession
) -> None:
    """No home secures it, so filed as nothing its interest is a loan's
    cost, not a mortgage's."""
    loan = await _loan(
        svc, async_db_session, owed=2_000_000, rate_bps=600, payment=50_000
    )
    checking = await seed_account(svc)
    day = current_date().replace(day=1)
    payment = await seed_txn(svc, checking.id, -50_000, day, name="AUTO LOAN PAYMENT")
    await seed_txn(svc, loan, 50_000, day, name="AUTO LOAN PAYMENT")

    await detect_transfers(async_db_session, owner_user_id=1, today=day)

    assert payment.id is not None
    filed = (
        await async_db_session.exec(
            select(FinanceCategory.name, FinanceTransactionSplit.memo)
            .join(
                FinanceCategory,
                FinanceCategory.id == FinanceTransactionSplit.category_id,
            )
            .where(
                FinanceTransactionSplit.parent_transaction_id == payment.id,
                FinanceTransactionSplit.memo == "Interest (estimated)",
            )
        )
    ).all()
    assert filed == [("Loans:Interest", "Interest (estimated)")]


@pytest.mark.asyncio
async def test_an_exact_payment_into_a_loan_is_paired_not_offered(
    svc: FinanceService, async_db_session: AsyncSession
) -> None:
    """Same amount, same day, nothing in the names: scored 70, so it was
    offered first, and the offer then blocked the loan's own history
    from pairing it - the payment counted whole as spending."""
    loan = await _loan(svc, async_db_session, owed=10_000_000, rate_bps=None)
    checking = await seed_account(svc)
    day = current_date() - timedelta(days=1)
    out = await seed_txn(svc, checking.id, -170_000, day, name="ROCKET MTG")
    await seed_txn(svc, loan, 170_000, day, name="ROCKET MTG")

    result = await detect_transfers(async_db_session, owner_user_id=1, today=day)

    transfers = (await async_db_session.exec(select(FinanceTransfer))).all()
    assert [t.status for t in transfers] == ["confirmed"]
    assert out.is_transfer is True
    assert result.payment_paired == 1
