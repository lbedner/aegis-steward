"""Reads and writes of transactions filed against matters and contacts
(``domains/ledger/links``)."""

from __future__ import annotations

from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.models import FinanceTransaction, FinanceTransactionLink


async def transaction_link_id(
    db: AsyncSession, transaction_id: int, label: str
) -> int | None:
    """The link filing ``transaction_id`` under ``label``, if there is one."""
    return (
        await db.exec(
            select(FinanceTransactionLink.id).where(
                FinanceTransactionLink.transaction_id == transaction_id,
                FinanceTransactionLink.label == label,
            )
        )
    ).first()


async def transactions_by_label(
    db: AsyncSession, labels: list[str]
) -> list[tuple[str, FinanceTransaction]]:
    """(label, transaction) for every live transaction filed under one of
    ``labels``, newest first, in one query."""
    return list(
        (
            await db.exec(
                select(FinanceTransactionLink.label, FinanceTransaction)
                .join(
                    FinanceTransaction,
                    col(FinanceTransaction.id) == FinanceTransactionLink.transaction_id,
                )
                .where(
                    col(FinanceTransactionLink.label).in_(labels),
                    col(FinanceTransaction.deleted_at).is_(None),
                )
                .order_by(col(FinanceTransaction.date_).desc())
            )
        ).all()
    )


async def delete_transaction_link(
    db: AsyncSession, transaction_id: int, label: str
) -> None:
    await db.exec(
        delete(FinanceTransactionLink).where(
            col(FinanceTransactionLink.transaction_id) == transaction_id,
            col(FinanceTransactionLink.label) == label,
        )
    )
