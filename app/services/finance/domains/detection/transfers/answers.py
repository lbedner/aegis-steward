"""A person's answer to a pairing (#317): confirm or turn down an
offered pair, or break a paired one, for good.

An offer and a pair are answered apart: an offer never touched its
rows, so turning it down leaves them as they are, and a stale Attention
button can never break a pair the person has not seen. A turned-down or
broken pair is kept as ``rejected`` rather than deleted: its legs stay
claimed, so detection never pairs them again on the next sync.
"""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection import queries
from app.services.finance.domains.ledger import changelog
from app.services.finance.domains.ledger.queries import live_transactions_by_ids
from app.services.finance.domains.ledger.splits import unsplit_transaction
from app.services.finance.domains.ledger.transactions import unpair_leg
from app.services.finance.models import FinanceTransaction, FinanceTransfer

from .shared import _flag_legs


async def _pair(
    db: AsyncSession,
    transaction_id: int,
    owner_user_id: int | None,
    answerable: tuple[str, ...],
) -> tuple[FinanceTransfer, FinanceTransaction, FinanceTransaction] | None:
    """The transfer ``transaction_id`` is a leg of, when it is in a state
    this answer is for and both legs are still live rows: an offer whose
    other half was deleted since is answered by nothing."""
    transfer = await queries.transfer_for_leg(db, transaction_id, owner_user_id)
    if transfer is None or transfer.id is None or transfer.status not in answerable:
        return None
    out_id, in_id = transfer.from_transaction_id, transfer.to_transaction_id
    if out_id is None or in_id is None:
        return None
    legs = {
        leg.id: leg
        for leg in await live_transactions_by_ids(
            db, [out_id, in_id], owner_user_id=owner_user_id
        )
    }
    out_leg, in_leg = legs.get(out_id), legs.get(in_id)
    if out_leg is None or in_leg is None:
        return None
    # Pairing is bookkeeping, not an edit to undo half of: the flags
    # move together or not at all.
    changelog.quietly(db, [out_leg, in_leg], "excluded_from_reports")
    return transfer, out_leg, in_leg


async def confirm_pair(
    db: AsyncSession, transaction_id: int, *, owner_user_id: int | None
) -> FinanceTransfer | None:
    """An offered pair, confirmed: both legs leave spending together."""
    found = await _pair(db, transaction_id, owner_user_id, ("suggested",))
    if found is None:
        return None
    transfer, out_leg, in_leg = found
    assert transfer.id is not None
    transfer.status = "confirmed"
    _flag_legs(out_leg, in_leg, transfer.id)
    db.add_all([transfer, out_leg, in_leg])
    await db.flush()
    return transfer


async def decline_offer(
    db: AsyncSession, transaction_id: int, *, owner_user_id: int | None
) -> FinanceTransfer | None:
    """Not a transfer: the offer is never made again. Its rows were
    never flagged, so they stay exactly as they are - a split, a row left
    out of reports."""
    found = await _pair(db, transaction_id, owner_user_id, ("suggested",))
    if found is None:
        return None
    transfer = found[0]
    transfer.status = "rejected"
    db.add(transfer)
    await db.flush()
    return transfer


async def break_pair(
    db: AsyncSession, transaction_id: int, *, owner_user_id: int | None
) -> FinanceTransfer | None:
    """Not a transfer: both legs count again, and the pair is never made
    again. A payment split as a loan's (#318) is whole again too: its
    principal line is only principal if it paid the loan."""
    found = await _pair(db, transaction_id, owner_user_id, ("confirmed",))
    if found is None:
        return None
    transfer, out_leg, in_leg = found
    # Paired yet counted, and split: the loan pass's work, not a person's.
    loan_split = [
        leg.id for leg in (out_leg, in_leg) if leg.is_split and not leg.is_transfer
    ]
    transfer.status = "rejected"
    for leg in (out_leg, in_leg):
        unpair_leg(leg)
    db.add_all([transfer, out_leg, in_leg])
    await db.flush()
    for leg_id in loan_split:
        if leg_id is not None:
            await unsplit_transaction(db, leg_id, owner_user_id=owner_user_id)
    return transfer
