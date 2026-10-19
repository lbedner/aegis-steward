"""A charge that will be paid back, and the money that pays it (#327).

An HSA reimbursing a medical bill, an employer a work expense, friends
their share: marked on the charge with what is expected and from whom,
then linked to the repayment(s). Once the repayments cover the whole
charge the pair nets out of spending - both leave the reports, the way
the rest of the app already leaves money out (``excluded_from_reports``),
and come back exactly as they were if a repayment is unlinked.

A share (a friend's $60 of a $100 dinner) settles too, but stays in
spending: leaving out part of one charge would need a split.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import changelog, queries
from app.services.finance.models import (
    FinanceReimbursement,
    FinanceReimbursementPayment,
    FinanceTransaction,
)
from app.services.finance.utils import utcnow


@dataclass(frozen=True)
class Reimbursable:
    """One reimbursable, its charge and what has come back so far."""

    reimbursement: FinanceReimbursement
    charge: FinanceTransaction
    repayments: list[FinanceTransaction]
    links: list[FinanceReimbursementPayment]

    @property
    def repaid(self) -> int:
        return sum(abs(t.amount) for t in self.repayments)

    @property
    def outstanding(self) -> int:
        return max(self.reimbursement.expected_amount - self.repaid, 0)


async def own_row(
    db: AsyncSession, owner_user_id: int | None, transaction_id: int
) -> FinanceTransaction:
    """The owner's live transaction, or ValueError."""
    txn = await db.get(FinanceTransaction, transaction_id)
    if txn is None or txn.deleted_at is not None or txn.owner_user_id != owner_user_id:
        raise ValueError(f"Transaction {transaction_id} not found.")
    return txn


async def reimbursable_for(
    db: AsyncSession, owner_user_id: int | None, transaction_id: int
) -> Reimbursable | None:
    """The reimbursable on this charge, or None."""
    rows = await reimbursables(db, owner_user_id, transaction_ids=[transaction_id])
    return rows[0] if rows else None


async def reimbursables(
    db: AsyncSession,
    owner_user_id: int | None,
    *,
    open_only: bool = False,
    transaction_ids: list[int] | None = None,
) -> list[Reimbursable]:
    """Reimbursables with their charges and repayments, oldest charge
    first: three reads, whatever the count."""
    marks = await queries.reimbursement_marks(
        db, owner_user_id, open_only=open_only, transaction_ids=transaction_ids
    )
    if not marks:
        return []
    links = await queries.reimbursement_payments(
        db, [m.id for m in marks if m.id is not None]
    )
    wanted = {m.transaction_id for m in marks} | {p.transaction_id for p in links}
    rows = await queries.transactions_by_ids(db, list(wanted))
    found = [
        Reimbursable(
            mark,
            rows[mark.transaction_id],
            [rows[p.transaction_id] for p in links if p.reimbursement_id == mark.id],
            [p for p in links if p.reimbursement_id == mark.id],
        )
        for mark in marks
        if mark.transaction_id in rows
    ]
    return sorted(found, key=lambda r: r.charge.date_)


async def mark(
    db: AsyncSession,
    owner_user_id: int | None,
    transaction_id: int,
    *,
    expected_amount: int | None = None,
    from_party_id: int | None = None,
    from_name: str | None = None,
    note: str | None = None,
) -> Reimbursable:
    """Mark a charge reimbursable, or restate what is expected of one
    already marked. The whole charge unless a share is named."""
    found = await reimbursable_for(db, owner_user_id, transaction_id)
    if found is None:
        charge = await own_row(db, owner_user_id, transaction_id)
        if charge.amount >= 0:
            raise ValueError("Only money that went out can be paid back.")
        row = FinanceReimbursement(
            owner_user_id=owner_user_id,
            transaction_id=transaction_id,
            expected_amount=abs(charge.amount),
        )
        found = Reimbursable(row, charge, [], [])
    else:
        _unsettle(db, found)  # restating what is expected settles afresh
    row, charge = found.reimbursement, found.charge
    if expected_amount is not None:
        if not 0 < expected_amount <= abs(charge.amount):
            raise ValueError("Expect between a cent and the whole charge.")
        row.expected_amount = expected_amount
    row.from_party_id = from_party_id
    row.from_name = (from_name or "").strip() or None
    row.note = (note or "").strip() or None
    db.add(row)
    _settle(db, found)
    await db.flush()
    return found


async def unmark(
    db: AsyncSession, owner_user_id: int | None, transaction_id: int
) -> None:
    """Not reimbursable after all: its repayments are let go, and the
    reports get back what settling took."""
    found = await reimbursable_for(db, owner_user_id, transaction_id)
    if found is None:
        return
    _unsettle(db, found)
    for link in found.links:
        await db.delete(link)
    await db.delete(found.reimbursement)
    await db.flush()


async def link_repayment(
    db: AsyncSession, owner_user_id: int | None, charge_id: int, repayment_id: int
) -> Reimbursable:
    """This money paid that charge back (or some of it)."""
    found = await reimbursable_for(db, owner_user_id, charge_id)
    if found is None:
        raise ValueError("That charge is not marked reimbursable.")
    repayment = await own_row(db, owner_user_id, repayment_id)
    if repayment.amount <= 0:
        raise ValueError("A repayment is money that came in.")
    link = FinanceReimbursementPayment(
        reimbursement_id=found.reimbursement.id or 0,
        transaction_id=repayment_id,
        was_excluded=repayment.excluded_from_reports,
    )
    db.add(link)
    found.repayments.append(repayment)
    found.links.append(link)
    _settle(db, found)
    await db.flush()
    return found


async def unlink_repayment(
    db: AsyncSession, owner_user_id: int | None, charge_id: int, repayment_id: int
) -> Reimbursable | None:
    found = await reimbursable_for(db, owner_user_id, charge_id)
    if found is None:
        return None
    _unsettle(db, found)
    for link in [lk for lk in found.links if lk.transaction_id == repayment_id]:
        await db.delete(link)
        found.links.remove(link)
    found.repayments[:] = [t for t in found.repayments if t.id != repayment_id]
    db.add(found.reimbursement)
    _settle(db, found)
    await db.flush()
    return found


def _settle(db: AsyncSession, found: Reimbursable) -> None:
    """Settled once repaid in full; a whole charge repaid nets the pair
    out of spending. The session flushes what it changes."""
    row = found.reimbursement
    if row.settled_at is not None or found.outstanding:
        return
    row.settled_at = utcnow()
    # A charge already left out was never counted: nothing to net.
    whole = row.expected_amount >= abs(found.charge.amount)
    if whole and not found.charge.excluded_from_reports:
        row.excluded_pair = True
        pair = [found.charge, *found.repayments]
        for txn in pair:
            txn.excluded_from_reports = True
        # The link is the action; the flags follow from it (#325).
        changelog.quietly(db, pair, "excluded_from_reports")


def _unsettle(db: AsyncSession, found: Reimbursable) -> None:
    """Put back what settling took: each row's own report flag (a charge
    the pair excluded was counted before it)."""
    row = found.reimbursement
    if row.excluded_pair:
        was = {p.transaction_id: p.was_excluded for p in found.links}
        found.charge.excluded_from_reports = False
        for txn in found.repayments:
            txn.excluded_from_reports = was.get(txn.id or 0, False)
        changelog.quietly(
            db, [found.charge, *found.repayments], "excluded_from_reports"
        )
        row.excluded_pair = False
    row.settled_at = None


async def repayment_candidates(
    db: AsyncSession, owner_user_id: int | None, found: Reimbursable, limit: int = 30
) -> list[FinanceTransaction]:
    """Money in after the charge, not already paying one back: what could
    be its repayment, newest first."""
    return await queries.unlinked_money_in(db, owner_user_id, found.charge.date_, limit)
