"""Pairing two legs of the same move across accounts."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.log import logger
from app.services.finance.domains.detection import queries
from app.services.finance.domains.detection.insights.formatting import format_usd
from app.services.finance.domains.detection.insights.rules.shared import (
    create_insight_if_new,
)
from app.services.finance.domains.ledger.queries.filters import not_duplicate
from app.services.finance.models import (
    FinanceAccount,
    FinanceTransaction,
    FinanceTransfer,
)
from app.services.shared.queries import owner_clause, stored_owner

from .offers import Offers
from .shared import (
    _CREDIT_CARD_TYPE,
    AUTO_THRESHOLD,
    SUGGEST_THRESHOLD,
    SUGGESTED,
    WINDOW_DAYS,
    TransferDetectionResult,
    _flag_legs,
    _score,
    _within_band,
    claimed_legs,
)


async def _pair_transfers(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    today: date,
    lookback_days: int,
) -> TransferDetectionResult:
    """The pairing pass: score and link opposite legs (see module doc)."""
    result = TransferDetectionResult()

    acct_filters = [
        FinanceAccount.deleted_at.is_(None),
        owner_clause(FinanceAccount.owner_user_id, owner_user_id),
    ]
    accounts = await queries.account_rows_where(db, acct_filters)
    account_type = {a.id: a.account_type for a in accounts}
    account_name = {a.id: a.name for a in accounts}
    if not account_type:
        return result

    # Legs already claimed by a transfer — excluded so pairings (including
    # rejected ones) never recur. Not a near miss's: a sure pair takes its
    # leg, and the offer goes (#317).
    transfers = await queries.transfers_for_owner(db, owner_user_id)
    paired_ids = claimed_legs(transfers, offers=False)
    offered = Offers(transfers)
    await offered.drop_dead(db)

    txn_filters = [
        FinanceTransaction.deleted_at.is_(None),
        not_duplicate(),
        FinanceTransaction.is_transfer.is_(False),
        FinanceTransaction.transfer_group_id.is_(None),
        FinanceTransaction.account_id.in_(list(account_type.keys())),
        owner_clause(FinanceTransaction.owner_user_id, owner_user_id),
    ]
    if lookback_days:
        txn_filters.append(
            FinanceTransaction.date_ >= today - timedelta(days=lookback_days)
        )
    txns = await queries.transaction_rows_where(db, txn_filters)
    candidates = [t for t in txns if t.id not in paired_ids]

    outflows = [t for t in candidates if t.amount < 0]
    inflows = [t for t in candidates if t.amount > 0]
    if not outflows or not inflows:
        return result

    # Score every plausible (outflow, inflow) pair, then greedily take the
    # highest-confidence pairs so each leg is used at most once per pass.
    scored: list[tuple[int, bool, FinanceTransaction, FinanceTransaction]] = []
    for out_txn in outflows:
        for in_txn in inflows:
            if out_txn.account_id == in_txn.account_id:
                continue  # a transfer moves between DIFFERENT accounts
            if out_txn.currency != in_txn.currency:
                continue  # $500 out and CA$500 in are not the same money
            if not _within_band(out_txn.amount, in_txn.amount):
                continue
            if abs((out_txn.date_ - in_txn.date_).days) > WINDOW_DAYS:
                continue
            in_on_card = account_type.get(in_txn.account_id) == _CREDIT_CARD_TYPE
            score, is_ccp = _score(out_txn, in_txn, in_on_card=in_on_card)
            if score >= SUGGEST_THRESHOLD:
                scored.append((score, is_ccp, out_txn, in_txn))

    # Highest first, so every sure pair is taken before a near miss can
    # claim one of its legs.
    scored.sort(key=lambda entry: entry[0], reverse=True)

    used: set[int] = set()
    for score, is_ccp, out_txn, in_txn in scored:
        if out_txn.id in used or in_txn.id in used:
            continue
        sure = score >= AUTO_THRESHOLD
        held = offered.holding(out_txn.id, in_txn.id)
        if held and not sure:
            continue  # offered already, this pair or another of its legs
        try:
            async with db.begin_nested():
                await offered.take(db, held)
                transfer = FinanceTransfer(
                    owner_user_id=owner_user_id,
                    organization_id=out_txn.organization_id,
                    from_account_id=out_txn.account_id,
                    to_account_id=in_txn.account_id,
                    from_transaction_id=out_txn.id,
                    to_transaction_id=in_txn.id,
                    amount=abs(out_txn.amount),
                    currency=out_txn.currency,
                    transfer_date=out_txn.date_,
                    is_credit_card_payment=is_ccp,
                    match_method="auto_amount_date",
                    confidence=score,
                    status="confirmed" if sure else "suggested",
                )
                db.add(transfer)
                await db.flush()
                if sure:
                    _flag_legs(out_txn, in_txn, transfer.id)
                    db.add(out_txn)
                    db.add(in_txn)
                    await db.flush()
                else:
                    await _offer(db, owner_user_id, transfer, account_name)
        except IntegrityError:
            # A leg was claimed by another transfer (race / prior pass). The
            # partial-uniques guarantee one transfer per leg — skip this pair.
            logger.debug("transfer pairing skipped: leg already paired")
            continue
        used.add(out_txn.id)
        used.add(in_txn.id)
        offered.taken_by(held)
        if sure:
            result.auto_paired += 1
        else:
            result.suggested += 1
    await offered.withdraw(db)
    return result


async def _offer(
    db: AsyncSession,
    owner_user_id: int | None,
    transfer: FinanceTransfer,
    account_name: dict[int | None, str],
) -> None:
    """A near miss, offered in Attention to confirm or turn down (#317).
    Until then both legs count, as they did."""
    amount = format_usd(transfer.amount)
    origin = account_name.get(transfer.from_account_id, "one account")
    destination = account_name.get(transfer.to_account_id, "another")
    await create_insight_if_new(
        db,
        owner_user_id=stored_owner(owner_user_id),
        insight_type=SUGGESTED,
        dedup_key=f"{SUGGESTED}:{transfer.id}",
        severity="info",
        title=f"Possible transfer: {amount} from {origin} to {destination}",
        body=(
            "The same money left one account and reached another close "
            "together. Confirm it and both sides leave spending; turn it "
            "down and they stay."
        ),
        detected_amount=transfer.amount,
        related_transaction_id=transfer.from_transaction_id,
    )
