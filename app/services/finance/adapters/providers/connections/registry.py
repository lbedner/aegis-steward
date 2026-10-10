"""Provider-agnostic verbs: disconnect one, sync one, sync them all.

The verbs dispatch on ``ADAPTERS`` - one ``ProviderAdapter`` per
aggregator, each declared beside its own sync package - so none of them
names a provider, and adding one is a new adapter in that table.

``_sync_isolated`` is the reason a batch sync is safe: one dead
connection returns ``None`` instead of taking the other accounts' data
down with it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import logging
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.services.finance.adapters.providers import queries
from app.services.finance.adapters.providers.connections import (
    plaid_sync,
    simplefin_sync,
    snaptrade_sync,
)
from app.services.finance.adapters.providers.connections.common import (
    ProviderAdapter,
    Revoke,
    SyncResult,
    _recompute_net_worth,
    get_connection,
    list_provider_connections,
    record_run,
)
from app.services.finance.domains.ledger import bank_link
from app.services.finance.models import FinanceConnection

logger = logging.getLogger(__name__)

ADAPTERS: dict[str, ProviderAdapter] = {
    adapter.provider: adapter
    for adapter in (plaid_sync.ADAPTER, snaptrade_sync.ADAPTER, simplefin_sync.ADAPTER)
}


def _client(adapter: ProviderAdapter, clients: Mapping[str, Any] | None) -> Any:
    """The client a caller injected for this provider, else a new one."""
    return (clients or {}).get(adapter.provider) or adapter.new_client()


async def disconnect_connection(
    db: AsyncSession,
    connection_id: int,
    *,
    owner_user_id: int | None = None,
    clients: Mapping[str, Any] | None = None,
) -> tuple[bool, Revoke | None]:
    """Disconnect a connection: soft-delete it and unlink its accounts,
    which stay with every row and simply stop updating (#307; the same
    bank linked again picks them back up), and return a best-effort provider revoke for the caller to
    run AFTER responding (FastAPI ``BackgroundTasks``). The provider round
    trip is the slow part of a disconnect; keeping it out of the request
    path makes the UI feel instant.

    Returns ``(removed, revoke)``: ``removed`` is False when the connection
    doesn't exist for this owner; ``revoke`` is None when there is nothing
    to revoke remotely. The revoke callable never raises (``best_effort``).
    """
    connection = await get_connection(db, connection_id, owner_user_id=owner_user_id)
    if connection is None:
        return False, None

    adapter = ADAPTERS.get(str(connection.provider))
    revoke = (
        adapter.revoke(connection, lambda: _client(adapter, clients))
        if adapter is not None
        else None
    )

    now = utcnow()
    accounts = await queries.live_accounts_for_connection(db, connection_id)
    for account in accounts:
        bank_link.unlink(account)
        db.add(account)

    connection.status = "revoked"
    connection.removed_at = now
    connection.deleted_at = now
    connection.access_token_encrypted = None
    db.add(connection)
    await db.flush()
    return True, revoke


async def _sync_isolated(
    db: AsyncSession,
    connection: FinanceConnection,
    sync: Callable[[], Awaitable[SyncResult]],
) -> SyncResult | None:
    """Run one connection's sync inside a SAVEPOINT.

    A failure rolls back only that connection's partial writes (preserving the
    all-or-nothing cursor invariant), marks the connection ``error`` with
    detail, and returns None — one failing bank never kills the others.
    """
    connection_id = connection.id
    owner_user_id = connection.owner_user_id
    provider = str(connection.provider)
    started = utcnow()
    try:
        async with db.begin_nested():
            result = await sync()
    except Exception as exc:
        logger.exception("Finance sync failed for connection %s", connection_id)
        connection.status = "error"
        connection.status_detail = str(exc)[:500]
        connection.last_error_code = getattr(exc, "error_code", None)
        connection.last_sync_attempt_at = utcnow()
        db.add(connection)
        record_run(
            db,
            connection_id=connection_id,
            owner_user_id=owner_user_id,
            provider=provider,
            started=started,
            result=None,
            error=str(exc)[:500],
        )
        await db.flush()
        return None
    logger.info(
        "Finance sync: connection %s -> %d account(s), +%d/%d/-%d txn(s), "
        "%d holding(s), %d trade(s)",
        connection_id,
        result.accounts,
        result.added,
        result.updated,
        result.removed,
        result.holdings,
        result.trades,
    )
    return result


async def sync_owner_connections(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    clients: Mapping[str, Any] | None = None,
) -> list[SyncResult]:
    """Sync every healthy provider connection for an owner.

    One read lists the owner's connections, grouped by provider. A
    provider's client is only built when it has a row to sync, so a
    single-provider deployment never touches another's credentials/SDK.
    Connections flagged ``needs_user_action`` are skipped (re-auth spam
    helps nobody); per-connection failures are isolated in
    ``_sync_isolated`` and absent from the returned results.
    """
    rows = await list_provider_connections(db, owner_user_id=owner_user_id)
    results: list[SyncResult] = []
    for adapter in ADAPTERS.values():
        syncable = [
            c
            for c in rows
            if c.provider == adapter.provider
            and adapter.ready(c)
            and not c.needs_user_action
        ]
        if not syncable:
            continue
        client = _client(adapter, clients)
        for connection in syncable:
            result = await _sync_isolated(
                db,
                connection,
                lambda c=connection: adapter.sync(db, c, client=client),
            )
            if result is not None:
                results.append(result)
    await _recompute_net_worth(db, owner_user_id, results)
    return results


async def sync_one_connection(
    db: AsyncSession,
    connection_id: int,
    *,
    owner_user_id: int | None = None,
    clients: Mapping[str, Any] | None = None,
) -> SyncResult | None:
    """Targeted sync of a single connection - the CLI debugging tool.

    Unlike the all-connections path this neither skips ``needs_user_action``
    nor swallows provider errors: when debugging one bank, the caller wants
    the real failure. Returns None when the connection is missing, foreign,
    or not syncable (manual / portal-pending).
    """
    connection = await get_connection(db, connection_id, owner_user_id=owner_user_id)
    if connection is None:
        return None
    adapter = ADAPTERS.get(str(connection.provider))
    if adapter is None or not adapter.ready(connection):
        return None
    result = await adapter.sync(db, connection, client=_client(adapter, clients))
    await _recompute_net_worth(db, connection.owner_user_id, [result])
    return result
