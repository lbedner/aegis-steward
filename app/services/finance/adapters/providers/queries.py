"""Batched read queries for provider connectivity (Plaid, SnapTrade).

Set-shaped inputs, map-shaped outputs. Statement builders only - no
business logic, no writes.
"""

from __future__ import annotations

from sqlalchemy import or_
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
