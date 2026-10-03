"""Permanent deletes of accounts and everything hanging off them
(``domains/ledger/accounts.purge_accounts``, #372).

The one write module here, because a purge is statements and nothing
else. The database's foreign keys cascade (migration 004), but the models
do not say so, and the schema a test builds from them has none: so every
dependent row is deleted, and every reference from a row that stays is
cleared, before its target goes - children first, the same on both.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, or_
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
        (FinanceImportBatchRow.account_id, account_ids),
        (FinanceImportBatch.account_id, account_ids),
        (FinanceLiabilityDetail.secured_by_account_id, account_ids),
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
    for column in (
        FinanceAttachment.account_id,
        FinanceInsight.related_account_id,
        FinanceTrade.account_id,
        FinanceHolding.account_id,
        FinanceBalanceSnapshot.account_id,
        FinanceValuation.account_id,
        FinanceLiabilityDetail.account_id,
        FinanceRecurringStream.account_id,
        FinanceAccount.id,
    ):
        await _drop(db, column, account_ids)
    await db.flush()
    return removed


async def _drop(db: AsyncSession, column: Any, ids: Select[Any] | list[int]) -> int:
    """Delete ``column``'s table's rows whose ``column`` is in ``ids``;
    how many went."""
    result = await db.exec(delete(column.class_).where(col(column).in_(ids)))
    return int(result.rowcount or 0)
