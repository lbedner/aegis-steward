"""Batched read queries for provider connectivity (Plaid, SnapTrade).

Set-shaped inputs, map-shaped outputs. Statement builders only - no
business logic, no writes.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.models import (
    FinanceAccount,
    FinanceConnection,
    FinanceTransaction,
)
from app.services.shared.queries import owner_filters


async def connection_by_provider_item(
    db: AsyncSession, *, provider: str, provider_item_id: str, live_only: bool = False
) -> FinanceConnection | None:
    query = select(FinanceConnection).where(
        FinanceConnection.provider == provider,
        FinanceConnection.provider_item_id == provider_item_id,
    )
    if live_only:
        query = query.where(FinanceConnection.deleted_at.is_(None))
    return (await db.exec(query)).first()


async def provider_accounts(
    db: AsyncSession, *, provider: str, owner_user_id: int | None, ids: list[str]
) -> list[FinanceAccount]:
    """The accounts a sync's accounts could already be, deleted ones too,
    oldest first, read once: the owner's (name + mask re-link), and any
    carrying one of the provider's ``ids`` (provider or persistent id).
    Without an owner, every account the provider ever fed."""
    query = select(FinanceAccount).where(FinanceAccount.provider == provider)
    if owner_user_id is not None:
        query = query.where(
            or_(
                FinanceAccount.owner_user_id == owner_user_id,
                col(FinanceAccount.provider_account_id).in_(ids),
                col(FinanceAccount.persistent_account_id).in_(ids),
            )
        )
    return list((await db.exec(query.order_by(col(FinanceAccount.id)))).all())


async def unlinked_accounts(
    db: AsyncSession, *, owner_user_id: int | None
) -> list[FinanceAccount]:
    """The live accounts no link feeds: the ones a file export does, and
    the ones a disconnect kept (``connections.placing``)."""
    query = select(FinanceAccount).where(
        FinanceAccount.connection_id.is_(None),
        FinanceAccount.deleted_at.is_(None),
    )
    if owner_user_id is not None:
        query = query.where(FinanceAccount.owner_user_id == owner_user_id)
    return list((await db.exec(query.order_by(col(FinanceAccount.name)))).all())


async def transaction_first_where(
    db: AsyncSession, filters: list
) -> FinanceTransaction | None:
    return (await db.exec(select(FinanceTransaction).where(*filters))).first()


async def provider_rows_for_accounts(
    db: AsyncSession, *, account_ids: set[int] | list[int], source: str
) -> list[FinanceTransaction]:
    """Live provider-sourced rows on the touched accounts - the sync
    dedup-lane preload."""
    if not account_ids:
        return []
    return list(
        (
            await db.exec(
                select(FinanceTransaction).where(
                    FinanceTransaction.account_id.in_(account_ids),
                    FinanceTransaction.source == source,
                    FinanceTransaction.deleted_at.is_(None),
                )
            )
        ).all()
    )


async def live_pending_rows(
    db: AsyncSession, *, account_ids: set[int], source: str
) -> list[FinanceTransaction]:
    """Live pending rows a feed brought to these accounts (#540)."""
    if not account_ids:
        return []
    return list(
        (
            await db.exec(
                select(FinanceTransaction).where(
                    FinanceTransaction.account_id.in_(account_ids),
                    FinanceTransaction.source == source,
                    FinanceTransaction.pending.is_(True),
                    FinanceTransaction.deleted_at.is_(None),
                )
            )
        ).all()
    )


async def unlinked_posted_rows(
    db: AsyncSession, *, account_ids: set[int], source: str, since: date
) -> list[FinanceTransaction]:
    """Live posted rows from ``since`` that replace no pre-auth yet: the
    twins a pending row that stopped being reported may have posted as."""
    return list(
        (
            await db.exec(
                select(FinanceTransaction).where(
                    FinanceTransaction.account_id.in_(account_ids),
                    FinanceTransaction.source == source,
                    FinanceTransaction.pending.is_(False),
                    FinanceTransaction.pending_transaction_id.is_(None),
                    FinanceTransaction.deleted_at.is_(None),
                    FinanceTransaction.date_ >= since,
                )
            )
        ).all()
    )


async def oldest_pending_day(db: AsyncSession, connection_id: int) -> date | None:
    """The day of the connection's oldest live pending row, if any."""
    return (
        await db.exec(
            select(func.min(FinanceTransaction.date_)).where(
                FinanceTransaction.connection_id == connection_id,
                FinanceTransaction.pending.is_(True),
                FinanceTransaction.deleted_at.is_(None),
            )
        )
    ).first()


async def connected_owner_ids(
    db: AsyncSession, providers: tuple[str, ...]
) -> list[int | None]:
    """Every owner with a live connection to one of ``providers``."""
    rows = (
        await db.exec(
            select(FinanceConnection.owner_user_id)
            .where(
                FinanceConnection.provider.in_(providers),
                FinanceConnection.deleted_at.is_(None),
            )
            .distinct()
        )
    ).all()
    return list(rows)


async def connections_for_owner(
    db: AsyncSession,
    *,
    provider: str | None = None,
    owner_user_id: int | None = None,
) -> list[FinanceConnection]:
    query = select(FinanceConnection).where(FinanceConnection.deleted_at.is_(None))
    if provider is not None:
        query = query.where(FinanceConnection.provider == provider)
    query = query.where(*owner_filters(FinanceConnection.owner_user_id, owner_user_id))
    return list((await db.exec(query)).all())


async def connection_by_id_live(
    db: AsyncSession, connection_id: int, *, owner_user_id: int | None = None
) -> FinanceConnection | None:
    query = select(FinanceConnection).where(
        FinanceConnection.id == connection_id,
        FinanceConnection.deleted_at.is_(None),
    )
    query = query.where(*owner_filters(FinanceConnection.owner_user_id, owner_user_id))
    return (await db.exec(query)).first()


async def live_accounts_for_connection(
    db: AsyncSession, connection_id: int
) -> list[FinanceAccount]:
    return list(
        (
            await db.exec(
                select(FinanceAccount).where(
                    FinanceAccount.connection_id == connection_id,
                    FinanceAccount.deleted_at.is_(None),
                )
            )
        ).all()
    )


async def move_children(
    db: AsyncSession, from_id: int, to_id: int, *, lines: bool = True
) -> None:
    """Hang what was set on a pre-auth - its tags, split lines, filings
    and a paid-back mark - on the row it posted as (#323). One the posted
    row already has stays its own: moving a second would break the key
    and fail the whole sync."""
    from sqlalchemy import delete, update

    from app.services.finance.models import (
        FinanceReimbursement,
        FinanceTransactionLink,
        FinanceTransactionSplit,
        FinanceTransactionTag,
    )

    tag, link = FinanceTransactionTag, FinanceTransactionLink
    for model, column, key in (
        (tag, tag.transaction_id, tag.tag_id),
        (link, link.transaction_id, link.label),
    ):
        held = select(key).where(column == to_id)
        await db.exec(delete(model).where(column == from_id, key.in_(held)))
        await db.exec(update(model).where(column == from_id).values({column: to_id}))
    mark = FinanceReimbursement
    if (
        await db.exec(select(mark.id).where(mark.transaction_id == to_id))
    ).first() is None:
        await db.exec(
            update(mark)
            .where(mark.transaction_id == from_id)
            .values(transaction_id=to_id)
        )
    if not lines:  # its split adds up to another amount: it stays behind
        return
    split = FinanceTransactionSplit
    await db.exec(
        update(split)
        .where(split.parent_transaction_id == from_id)
        .values(parent_transaction_id=to_id)
    )
