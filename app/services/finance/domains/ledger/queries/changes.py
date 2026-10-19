"""Reads behind the change log, reimbursables and saved views (#325-#327)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.models import (
    FinanceRegisterView,
    FinanceReimbursement,
    FinanceReimbursementPayment,
    FinanceTransaction,
    FinanceTransactionChangelog,
    FinanceTransactionTag,
)
from app.services.shared.queries import owner_filters

_NEWEST = FinanceTransactionChangelog.id.desc()  # type: ignore[union-attr]


async def changes_where(
    db: AsyncSession, *filters: object, limit: int | None = None, order: Any = None
) -> list[FinanceTransactionChangelog]:
    """Change-log rows, newest first (or in ``order``)."""
    query = (
        select(FinanceTransactionChangelog)
        .where(*filters)
        .order_by(_NEWEST if order is None else order)
    )
    if limit is not None:
        query = query.limit(limit)
    return list((await db.exec(query)).all())


async def tags_on(
    db: AsyncSession, transaction_ids: Iterable[int]
) -> list[FinanceTransactionTag]:
    """Every tag link on these transactions."""
    wanted = set(transaction_ids)
    if not wanted:
        return []
    return list(
        (
            await db.exec(
                select(FinanceTransactionTag).where(
                    FinanceTransactionTag.transaction_id.in_(wanted)
                )
            )
        ).all()
    )


async def reimbursement_marks(
    db: AsyncSession,
    owner_user_id: int | None,
    *,
    open_only: bool = False,
    transaction_ids: list[int] | None = None,
) -> list[FinanceReimbursement]:
    query = select(FinanceReimbursement).where(
        *owner_filters(FinanceReimbursement.owner_user_id, owner_user_id),
        # A deleted charge is owed nothing: a pre-auth that never posted.
        FinanceReimbursement.transaction_id.in_(
            select(FinanceTransaction.id).where(FinanceTransaction.deleted_at.is_(None))
        ),
    )
    if open_only:
        query = query.where(FinanceReimbursement.settled_at.is_(None))
    if transaction_ids is not None:
        query = query.where(FinanceReimbursement.transaction_id.in_(transaction_ids))
    return list((await db.exec(query)).all())


async def reimbursement_payments(
    db: AsyncSession, reimbursement_ids: list[int]
) -> list[FinanceReimbursementPayment]:
    if not reimbursement_ids:
        return []
    return list(
        (
            await db.exec(
                select(FinanceReimbursementPayment).where(
                    FinanceReimbursementPayment.reimbursement_id.in_(reimbursement_ids)
                )
            )
        ).all()
    )


async def unlinked_money_in(
    db: AsyncSession, owner_user_id: int | None, since: object, limit: int
) -> list[FinanceTransaction]:
    """Money in from ``since``, not already paying a charge back, newest
    first: what could be a repayment."""
    linked = select(FinanceReimbursementPayment.transaction_id)
    return list(
        (
            await db.exec(
                select(FinanceTransaction)
                .where(
                    *owner_filters(FinanceTransaction.owner_user_id, owner_user_id),
                    FinanceTransaction.deleted_at.is_(None),
                    FinanceTransaction.amount > 0,
                    FinanceTransaction.date_ >= since,
                    FinanceTransaction.id.not_in(linked),
                )
                .order_by(FinanceTransaction.date_.desc())
                .limit(limit)
            )
        ).all()
    )


async def views_for(
    db: AsyncSession, owner_user_id: int | None
) -> list[FinanceRegisterView]:
    return list(
        (
            await db.exec(
                select(FinanceRegisterView)
                .where(*owner_filters(FinanceRegisterView.owner_user_id, owner_user_id))
                .order_by(FinanceRegisterView.name)
            )
        ).all()
    )
