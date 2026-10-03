"""One sync pass over a SnapTrade connection.

Also home to the two credential helpers: the ``user_secret`` is the
actual credential, AES-GCM encrypted per connection row, and both the
sync and the connect flow need to read it back.

Writes but does not commit - the caller owns the transaction.
"""

from collections.abc import Callable
from datetime import date, timedelta
import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.encryption import decrypt_secret
from app.core.time import utcnow
from app.services.finance.adapters.providers.connections.common import (
    _SNAPTRADE_SECRET_CONTEXT,
    ProviderAdapter,
    Revoke,
    SyncResult,
    best_effort,
    finished,
    list_provider_connections,
    stored_credential,
)
from app.services.finance.adapters.providers.connections.snaptrade_sync.accounts import (
    snaptrade_accounts,
)
from app.services.finance.adapters.providers.connections.snaptrade_sync.investments import (
    _apply_snaptrade_activities,
    _apply_snaptrade_positions,
)
from app.services.finance.adapters.providers.connections.upserts import (
    upsert_accounts,
)
from app.services.finance.adapters.providers.snaptrade import (
    SnapTradeClient,
)
from app.services.finance.constants import Provider
from app.services.finance.models import FinanceConnection
from app.services.finance.service import FinanceService

logger = logging.getLogger(__name__)

# First sync pulls this trailing window of activities; SnapTrade has no cursor,
# so later syncs re-window from the last pull (minus a small overlap for
# late-posting rows) and dedup on the activity id.
_SNAPTRADE_LOOKBACK_DAYS = 730
_SNAPTRADE_ACTIVITY_OVERLAP_DAYS = 7
_SNAPTRADE_ACTIVITY_PAGE = 500


def _snaptrade_user_id(owner_user_id: int | None) -> str:
    """The immutable SnapTrade ``userId`` for an app user. Deterministic so it
    never needs storing; SnapTrade scopes user ids to the partner app."""
    return "user-standalone" if owner_user_id is None else f"user-{owner_user_id}"


async def _snaptrade_user_secret(
    db: AsyncSession, *, owner_user_id: int | None
) -> str | None:
    """The owner's SnapTrade ``userSecret``, from any of their connection rows
    (every row stores the same user-level secret)."""
    for connection in await list_provider_connections(
        db, provider=Provider.SNAPTRADE, owner_user_id=owner_user_id
    ):
        if connection.access_token_encrypted:
            return decrypt_secret(
                connection.access_token_encrypted, context=_SNAPTRADE_SECRET_CONTEXT
            )
    return None


async def sync_snaptrade_connection(
    db: AsyncSession,
    connection: FinanceConnection,
    *,
    client: SnapTradeClient | None = None,
) -> SyncResult:
    """Sync one SnapTrade authorization: accounts + positions every run,
    activities at most once per day per account.

    SnapTrade's launch guide budgets polling (holdings a few times a day,
    activities ~daily) and refreshes its own upstream cache daily anyway.
    ``sync_cursor`` stores the date of the last activities pull: the window
    re-opens from there (minus a small overlap) and the activity-id dedup
    absorbs the overlap, mirroring the Plaid investments lane.
    """
    started = utcnow()
    client = client or SnapTradeClient()
    service = FinanceService(db)
    result = SyncResult(connection_id=connection.id)
    connection.last_sync_attempt_at = utcnow()
    if not connection.access_token_encrypted or not connection.provider_item_id:
        return result
    user_id = "" if client.is_personal else _snaptrade_user_id(connection.owner_user_id)
    user_secret = decrypt_secret(
        connection.access_token_encrypted, context=_SNAPTRADE_SECRET_CONTEXT
    )

    accounts = [
        account
        for account in await client.list_accounts(user_id, user_secret)
        if str(account.get("brokerage_authorization") or "")
        == connection.provider_item_id
    ]
    account_map = await upsert_accounts(
        db, service, connection, Provider.SNAPTRADE, snaptrade_accounts(accounts)
    )
    result.accounts = len(account_map)

    today = utcnow().date()
    last_pull = (
        date.fromisoformat(connection.sync_cursor) if connection.sync_cursor else None
    )
    pull_activities = last_pull is None or last_pull < today
    start = (
        today - timedelta(days=_SNAPTRADE_LOOKBACK_DAYS)
        if last_pull is None
        else last_pull - timedelta(days=_SNAPTRADE_ACTIVITY_OVERLAP_DAYS)
    )

    for snaptrade_id, account_id in account_map.items():
        positions = await client.get_positions(user_id, user_secret, snaptrade_id)
        result.holdings += await _apply_snaptrade_positions(
            service,
            positions,
            account_id=account_id,
            owner_user_id=connection.owner_user_id,
        )
        if not pull_activities:
            continue
        offset = 0
        while True:
            page = await client.get_activities(
                user_id,
                user_secret,
                snaptrade_id,
                start_date=start.isoformat(),
                end_date=today.isoformat(),
                offset=offset,
                limit=_SNAPTRADE_ACTIVITY_PAGE,
            )
            batch = page.get("data") or []
            result.trades += await _apply_snaptrade_activities(
                service, batch, account_id=account_id, connection=connection
            )
            offset += len(batch)
            total = (page.get("pagination") or {}).get("total")
            if not batch or len(batch) < _SNAPTRADE_ACTIVITY_PAGE:
                break
            if total is not None and offset >= int(total):
                break

    if pull_activities:
        connection.sync_cursor = today.isoformat()
    finished(db, connection, started=started, result=result)
    db.add(connection)
    await db.flush()
    return result


def _revoke(
    connection: FinanceConnection, client: Callable[[], SnapTradeClient]
) -> Revoke | None:
    if not connection.provider_item_id:
        return None
    secret = stored_credential(connection, _SNAPTRADE_SECRET_CONTEXT)
    if secret is None:
        return None
    authorization_id, owner = connection.provider_item_id, connection.owner_user_id

    async def remove() -> None:
        snaptrade = client()
        user_id = "" if snaptrade.is_personal else _snaptrade_user_id(owner)
        await snaptrade.remove_authorization(user_id, secret, authorization_id)

    return best_effort("SnapTrade", connection.id, remove)


async def _sync(
    db: AsyncSession, connection: FinanceConnection, client: SnapTradeClient
) -> SyncResult:
    return await sync_snaptrade_connection(db, connection, client=client)


ADAPTER = ProviderAdapter(
    provider=Provider.SNAPTRADE,
    new_client=SnapTradeClient,
    sync=_sync,
    # Rows still waiting on the portal (no authorization yet) can't sync.
    ready=lambda connection: connection.provider_item_id is not None,
    revoke=_revoke,
)
