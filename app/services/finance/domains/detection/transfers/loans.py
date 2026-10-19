"""A loan payment, split into principal, interest and escrow (#318).

Only principal is money moved; interest and escrow are what the loan -
the house, for a mortgage - costs. Two shapes reach here:

* The loan's register shows the whole payment, so it paired as a
  transfer and left spending whole: a mortgage read as $0 of housing.
  With a rate on file the cash side comes back as its interest
  (estimated from the balance and rate) and escrow (as stated), and
  only the principal stays a transfer.
* The loan's register shows only what the balance came down by - the
  bank's principal - and the payment never paired, counting whole. It
  pairs with the payment the loan expects, and the payment splits on
  the bank's own principal; the rest is interest and escrow.

The cash side keeps its pairing (it still opens its other half) and is
split rather than flagged, so every split-aware read counts the lines:
principal under a transfer category, the rest as spending. An estimated
line says so in its memo until someone edits the split.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import union
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.log import logger
from app.services.finance.domains.detection import queries
from app.services.finance.domains.ledger import queries as ledger_queries
from app.services.finance.domains.ledger.categories import (
    get_or_create_category_from_hint,
)
from app.services.finance.domains.ledger.queries.filters import not_duplicate
from app.services.finance.domains.ledger.splits import write_lines
from app.services.finance.domains.ledger.transactions import count_again
from app.services.finance.models import (
    FinanceAccount,
    FinanceLiabilityDetail,
    FinanceTransaction,
    FinanceTransfer,
)
from app.services.finance.schemas import SplitPart
from app.services.shared.queries import owner_clause

from .shared import (
    AMOUNT_EXACT_TOLERANCE_CENTS,
    AUTO_THRESHOLD,
    WINDOW_DAYS,
    _flag_legs,
)

PRINCIPAL = "Transfer:Loan principal"
# Where the cost goes when the payment was filed as nothing that spends:
# a loan a home secures is housing; a car or student loan is not.
HOME_COST = "Housing:Mortgage"
LOAN_COST = "Loans:Interest"


def _cost_hint(detail: FinanceLiabilityDetail) -> str:
    """Housing for a mortgage, loan interest for any other loan."""
    if detail.secured_by_account_id or detail.liability_type == "mortgage":
        return HOME_COST
    return LOAN_COST


def _expected(detail: FinanceLiabilityDetail, payment: FinanceTransaction) -> bool:
    """Whether ``payment`` is the one the loan expects each month: only
    that payment carries a month's interest and escrow. An extra payment
    to principal, a payoff or a half of a biweekly pair carries neither."""
    expected = detail.minimum_payment_amount
    return bool(expected) and (
        abs(abs(payment.amount) - expected) <= AMOUNT_EXACT_TOLERANCE_CENTS
    )


def monthly_interest(balance: int, rate_bps: int) -> int:
    """A month's interest on ``balance`` at ``rate_bps`` a year, in cents."""
    return round(abs(balance) * rate_bps / 120_000)


async def _split_loan_payments(db: AsyncSession, *, owner_user_id: int | None) -> int:
    """Split every loan payment not split yet; the number split."""
    accounts = await queries.account_rows_where(
        db,
        [
            FinanceAccount.deleted_at.is_(None),
            owner_clause(FinanceAccount.owner_user_id, owner_user_id),
        ],
    )
    loans = {a.id: a for a in accounts if a.account_type == "loan" and a.id}
    if not loans:
        return 0
    cash = [a.id for a in accounts if a.classification == "asset" and a.id]
    details = await ledger_queries.liability_details_by_account(db, list(loans))
    balances = await _balances(db, loans)
    names = _Names(db, owner_user_id)
    split = await _whole_payments(db, loans, details, balances, names)
    split += await _principal_credits(
        db, owner_user_id, loans, cash, details, balances, names
    )
    if split:
        await db.flush()
    return split


async def _balances(
    db: AsyncSession, loans: dict[int, FinanceAccount]
) -> dict[tuple[int, date], int]:
    """Each loan's balance by day, where the net-worth line drew one."""
    rows = await ledger_queries.balance_snapshots_between(
        db, list(loans), start=date.min, end=date.max
    )
    return {(r.account_id, r.balance_date): r.balance for r in rows}


def _interest(
    loan: FinanceAccount,
    detail: FinanceLiabilityDetail,
    day: date,
    balances: dict[tuple[int, date], int],
) -> int | None:
    """A payment's interest, estimated from what was owed the day before
    ``day`` (today's balance where the line has no figure for then) at
    the loan's rate. None when either is unknown: a guess of zero would
    file the whole payment as principal, the bug this exists to fix."""
    assert loan.id is not None
    owed = balances.get((loan.id, day - timedelta(days=1)), loan.current_balance)
    if not owed or not detail.interest_rate_bps:
        return None
    return monthly_interest(owed, detail.interest_rate_bps)


class _Names:
    """The two categories a split files under, made once a pass."""

    def __init__(self, db: AsyncSession, owner_user_id: int | None) -> None:
        self.db, self.owner = db, owner_user_id
        self.found: dict[str, int | None] = {}
        self.spending: set[int] | None = None  # read the first time asked

    async def __call__(self, hint: str) -> int | None:
        if hint not in self.found:
            category = await get_or_create_category_from_hint(
                self.db, hint, owner_user_id=self.owner
            )
            self.found[hint] = category.id if category else None
        return self.found[hint]

    async def cost(
        self, payment: FinanceTransaction, detail: FinanceLiabilityDetail
    ) -> int | None:
        """Where interest and escrow go: what the payment was filed
        under, when that spends; the loan's own cost otherwise."""
        if self.spending is None:
            self.spending = await queries.expense_category_ids(self.db)
        if payment.category_id in self.spending:
            return payment.category_id
        return await self(_cost_hint(detail))


async def _whole_payments(
    db: AsyncSession,
    loans: dict[int, FinanceAccount],
    details: dict[int, FinanceLiabilityDetail],
    balances: dict[tuple[int, date], int],
    names: _Names,
) -> int:
    """Paired payments that took the whole amount out of spending: the
    one the loan expects, of a loan with a rate on file."""
    rated = [
        i
        for i, d in details.items()
        if d.interest_rate_bps and d.minimum_payment_amount
    ]
    transfers = await queries.confirmed_transfers_into(db, rated)
    legs = await ledger_queries.transactions_by_ids(
        db, [t.from_transaction_id for t in transfers if t.from_transaction_id]
    )
    split = 0
    for transfer in transfers:
        payment = legs.get(transfer.from_transaction_id or 0)
        if (
            payment is None
            or payment.deleted_at is not None
            or payment.is_split
            or not payment.is_transfer
        ):
            continue
        detail = details[transfer.to_account_id or 0]
        loan = loans[transfer.to_account_id or 0]
        if not _expected(detail, payment):
            continue
        interest = _interest(loan, detail, payment.date_, balances)
        escrow = detail.escrow_amount or 0
        if interest is None or abs(payment.amount) - interest - escrow <= 0:
            continue
        principal = abs(payment.amount) - interest - escrow
        cost = await names.cost(payment, detail)
        lines = [
            SplitPart(
                amount=principal,
                category_id=await names(PRINCIPAL),
                memo="Principal (estimated)",
            ),
            SplitPart(amount=interest, category_id=cost, memo="Interest (estimated)"),
        ]
        if escrow:
            lines.append(SplitPart(amount=escrow, category_id=cost, memo="Escrow"))
        count_again(payment)
        write_lines(db, payment, [line for line in lines if line.amount > 0])
        split += 1
    return split


async def _principal_credits(
    db: AsyncSession,
    owner_user_id: int | None,
    loans: dict[int, FinanceAccount],
    cash: list[int],
    details: dict[int, FinanceLiabilityDetail],
    balances: dict[tuple[int, date], int],
    names: _Names,
) -> int:
    """A loan's principal credit, paired with the payment it came from."""
    expecting = [i for i, d in details.items() if d.minimum_payment_amount]
    if not expecting or not cash:
        return 0
    # Legs no transfer holds, of any status: one a suggestion or a
    # turned-down pair holds would fail its insert and leave the credit
    # unpaired on every pass.
    open_leg = [
        FinanceTransaction.deleted_at.is_(None),
        not_duplicate(),
        FinanceTransaction.transfer_group_id.is_(None),
        FinanceTransaction.id.notin_(_held_legs()),
        owner_clause(FinanceTransaction.owner_user_id, owner_user_id),
    ]
    credits = await queries.transaction_rows_where(
        db,
        [
            *open_leg,
            FinanceTransaction.account_id.in_(expecting),
            FinanceTransaction.amount > 0,
        ],
    )
    if not credits:
        return 0
    payments = await queries.transaction_rows_where(
        db,
        [
            *open_leg,
            FinanceTransaction.account_id.in_(cash),
            FinanceTransaction.amount < 0,
            FinanceTransaction.is_split.is_(False),
            FinanceTransaction.date_
            >= min(c.date_ for c in credits) - timedelta(days=WINDOW_DAYS),
        ],
    )
    used: set[int | None] = set()
    split = 0
    for credit in sorted(credits, key=lambda c: c.date_):
        payment = _nearest(credit, payments, details[credit.account_id], used)
        if payment is None:
            continue
        transfer = await _pair(db, payment, credit)
        if transfer is None:
            continue
        used.add(payment.id)
        detail, loan = details[credit.account_id], loans[credit.account_id]
        rest = abs(payment.amount) - credit.amount
        cost = await names.cost(payment, detail)
        lines = [
            SplitPart(
                amount=credit.amount,
                category_id=await names(PRINCIPAL),
                memo="Principal",
            ),
        ]
        estimate = _interest(loan, detail, payment.date_, balances)
        if estimate is not None:
            interest = min(rest, estimate)
            lines.append(
                SplitPart(
                    amount=interest, category_id=cost, memo="Interest (estimated)"
                )
            )
            if rest > interest:
                lines.append(
                    SplitPart(
                        amount=rest - interest,
                        category_id=cost,
                        memo="Escrow and fees (estimated)",
                    )
                )
        else:
            lines.append(
                SplitPart(amount=rest, category_id=cost, memo="Interest and escrow")
            )
        # Filed as a transfer, the category pass took it out of every
        # total; its lines are what count now.
        count_again(payment)
        write_lines(db, payment, [line for line in lines if line.amount > 0])
        split += 1
    return split


def _held_legs() -> Any:
    """Subquery: every leg a transfer holds, of any status."""
    legs = FinanceTransfer.from_transaction_id, FinanceTransfer.to_transaction_id
    return union(*(select(leg).where(leg.is_not(None)) for leg in legs))


def _nearest(
    credit: FinanceTransaction,
    payments: list[FinanceTransaction],
    detail: FinanceLiabilityDetail,
    used: set[int | None],
) -> FinanceTransaction | None:
    """The payment the loan expected, nearest the credit, larger than it."""
    fits = [
        p
        for p in payments
        if p.id not in used
        and _expected(detail, p)
        and abs(p.amount) > credit.amount
        and abs((p.date_ - credit.date_).days) <= WINDOW_DAYS
    ]
    return min(fits, key=lambda p: abs((p.date_ - credit.date_).days), default=None)


async def _pair(
    db: AsyncSession, payment: FinanceTransaction, credit: FinanceTransaction
) -> FinanceTransfer | None:
    """The two legs as a confirmed transfer of the principal. The credit
    leaves reports (it is the principal, moved); the payment keeps
    counting, through the lines it is split into."""
    try:
        async with db.begin_nested():
            transfer = FinanceTransfer(
                owner_user_id=payment.owner_user_id,
                organization_id=payment.organization_id,
                from_account_id=payment.account_id,
                to_account_id=credit.account_id,
                from_transaction_id=payment.id,
                to_transaction_id=credit.id,
                amount=credit.amount,
                currency=payment.currency,
                transfer_date=payment.date_,
                match_method="payment_history",
                confidence=AUTO_THRESHOLD,
                status="confirmed",
            )
            db.add(transfer)
            await db.flush()
            assert transfer.id is not None
            _flag_legs(payment, credit, transfer.id)
            count_again(payment)  # it counts through its lines
            db.add_all([payment, credit])
            await db.flush()
    except IntegrityError:
        logger.debug("loan payment pairing skipped: leg already paired")
        return None
    return transfer
