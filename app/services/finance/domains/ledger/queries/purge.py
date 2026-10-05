"""Permanent deletes of accounts and everything hanging off them
(``domains/ledger/accounts.purge_accounts``, #372), and merges of one
account into another (``domains/ledger/merging``, #309).

The one write module here, because both are statements and nothing
else - over the one list of what points at an account
(``ACCOUNT_COLUMNS``), which a test holds to the schema's foreign keys. The database's foreign keys cascade (migration 004), but the models
do not say so, and the schema a test builds from them has none: so every
dependent row is deleted, and every reference from a row that stays is
cleared, before its target goes - children first, the same on both.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, and_, bindparam, or_
from sqlalchemy.orm import aliased
from sqlmodel import col, delete, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.models import (
    FinanceAccount,
    FinanceAttachment,
    FinanceBalanceSnapshot,
    FinanceHolding,
    FinanceImportBatch,
    FinanceImportBatchRow,
    FinanceInsight,
    FinanceLiabilityDetail,
    FinanceRecurringStream,
    FinanceTrade,
    FinanceTransaction,
    FinanceTransactionChangelog,
    FinanceTransactionLink,
    FinanceTransactionSplit,
    FinanceTransactionTag,
    FinanceTransfer,
    FinanceValuation,
)

# Rows that only REFER to an account: a purge clears them, a merge points
# them at the account that stays.
REFERS_TO_ACCOUNT = (
    FinanceImportBatchRow.account_id,
    FinanceImportBatch.account_id,
    FinanceLiabilityDetail.secured_by_account_id,
)
# Rows that BELONG to an account: a purge deletes them (children first),
# a merge moves them.
BELONGS_TO_ACCOUNT = (
    FinanceAttachment.account_id,
    FinanceInsight.related_account_id,
    FinanceTrade.account_id,
    FinanceHolding.account_id,
    FinanceBalanceSnapshot.account_id,
    FinanceValuation.account_id,
    FinanceLiabilityDetail.account_id,
    FinanceRecurringStream.account_id,
)
ACCOUNT_COLUMNS = (
    FinanceTransaction.account_id,
    FinanceTransfer.from_account_id,
    FinanceTransfer.to_account_id,
    *REFERS_TO_ACCOUNT,
    *BELONGS_TO_ACCOUNT,
)
# Tables holding one row per account (and these columns): where both
# accounts of a merge have one, the staying account's is kept.
ONE_PER_ACCOUNT: dict[Any, tuple[str, ...]] = {
    FinanceLiabilityDetail.account_id: (),
    FinanceBalanceSnapshot.account_id: ("balance_date",),
    FinanceValuation.account_id: ("as_of_date", "source"),
    FinanceHolding.account_id: ("security_id", "as_of_date"),
}


async def _fold_streams(db: AsyncSession, gone: int, stays: int) -> None:
    """A stream detected on both accounts (one per payee and direction an
    account) becomes ``stays``'s: what pointed at ``gone``'s copy points at
    it, and the copy goes."""
    copy, kept = FinanceRecurringStream, aliased(FinanceRecurringStream)
    folds = (
        await db.exec(
            select(copy.id, kept.id)
            .join(
                kept,
                and_(
                    kept.account_id == stays,
                    kept.owner_user_id == copy.owner_user_id,
                    kept.direction == copy.direction,
                    kept.normalized_payee == copy.normalized_payee,
                    col(kept.provider_stream_id).is_(None),
                ),
            )
            .where(copy.account_id == gone, col(copy.provider_stream_id).is_(None))
        )
    ).all()
    if not folds:
        return
    moves = [{"was": was, "now": now} for was, now in folds]
    for column in (
        FinanceTransaction.recurring_stream_id,
        FinanceInsight.related_stream_id,
    ):
        table = column.class_.__table__
        await db.execute(
            update(table)
            .where(table.c[column.key] == bindparam("was"))
            .values({column.key: bindparam("now")}),
            moves,
        )
    await db.exec(delete(copy).where(col(copy.id).in_([m["was"] for m in moves])))


async def repoint_account(db: AsyncSession, gone: int, stays: int) -> None:
    """Everything pointing at account ``gone`` points at ``stays`` - but a
    one-per-account row ``stays`` already has drops ``gone``'s copy, and a
    stream both have folds into ``stays``'s. Writes but does not commit."""
    await _fold_streams(db, gone, stays)
    for column, keys in ONE_PER_ACCOUNT.items():
        table = column.class_
        theirs = aliased(table)
        clash = (
            select(getattr(theirs, "id"))
            .where(
                getattr(theirs, column.key) == stays,
                *(getattr(theirs, key) == getattr(table, key) for key in keys),
            )
            .exists()
        )
        await db.exec(delete(table).where(col(column) == gone, clash))
    for column in ACCOUNT_COLUMNS:
        await db.exec(
            update(column.class_).where(col(column) == gone).values({column.key: stays})
        )
    await db.flush()


async def account_ids_for_connection(
    db: AsyncSession, connection_id: int, *, owner_user_id: int | None = None
) -> list[int]:
    """Every account a connection ever fed, removed ones too: only the
    owner's, when one is named."""
    query = select(FinanceAccount.id).where(
        FinanceAccount.connection_id == connection_id
    )
    if owner_user_id is not None:
        query = query.where(FinanceAccount.owner_user_id == owner_user_id)
    return [int(i) for i in (await db.exec(query)).all() if i is not None]


async def purge_accounts(db: AsyncSession, account_ids: list[int]) -> int:
    """Delete ``account_ids`` and every row that hangs off them; return how
    many transactions went. Writes but does not commit."""
    if not account_ids:
        return 0
    txns = select(FinanceTransaction.id).where(
        col(FinanceTransaction.account_id).in_(account_ids)
    )
    streams = select(FinanceRecurringStream.id).where(
        col(FinanceRecurringStream.account_id).in_(account_ids)
    )
    trades = select(FinanceTrade.id).where(
        col(FinanceTrade.account_id).in_(account_ids)
    )
    transfers = select(FinanceTransfer.id).where(
        or_(
            col(FinanceTransfer.from_transaction_id).in_(txns),
            col(FinanceTransfer.to_transaction_id).in_(txns),
            col(FinanceTransfer.from_account_id).in_(account_ids),
            col(FinanceTransfer.to_account_id).in_(account_ids),
        )
    )
    # Rows that stay, pointing at rows that go.
    for column, ids in (
        (FinanceTransaction.canonical_transaction_id, txns),
        (FinanceTransaction.transfer_pair_transaction_id, txns),
        (FinanceTransaction.reverses_transaction_id, txns),
        (FinanceTransaction.pending_transaction_id, txns),
        (FinanceTransaction.recurring_stream_id, streams),
        (FinanceTransaction.transfer_group_id, transfers),
        (FinanceTrade.transaction_id, txns),
        (FinanceImportBatchRow.matched_transaction_id, txns),
        (FinanceImportBatchRow.matched_trade_id, trades),
        *((column, account_ids) for column in REFERS_TO_ACCOUNT),
    ):
        await db.exec(
            update(column.class_).where(col(column).in_(ids)).values({column.key: None})
        )
    # Rows that go: what hangs off the transactions, then the transactions,
    # then what hangs off the accounts, then the accounts.
    for column, ids in (
        (FinanceTransactionTag.transaction_id, txns),
        (FinanceTransactionLink.transaction_id, txns),
        (FinanceTransactionSplit.parent_transaction_id, txns),
        (FinanceTransactionChangelog.transaction_id, txns),
        (FinanceAttachment.transaction_id, txns),
        (FinanceInsight.related_transaction_id, txns),
        (FinanceInsight.related_stream_id, streams),
        (FinanceTransfer.id, transfers),
    ):
        await _drop(db, column, ids)
    removed = await _drop(db, FinanceTransaction.account_id, account_ids)
    for column in (*BELONGS_TO_ACCOUNT, FinanceAccount.id):
        await _drop(db, column, account_ids)
    await db.flush()
    return removed


async def _drop(db: AsyncSession, column: Any, ids: Select[Any] | list[int]) -> int:
    """Delete ``column``'s table's rows whose ``column`` is in ``ids``;
    how many went."""
    result = await db.exec(delete(column.class_).where(col(column).in_(ids)))
    return int(result.rowcount or 0)
