"""One sync pass over a SimpleFIN connection, and connecting one.

One access URL covers every bank the user linked at SimpleFIN, so one
connection row holds it (encrypted) and one request a pass reads them
all - the Bridge budgets about 24 a day. There is no cursor: each pass
re-reads from the last one minus a few days of overlap, and the shared
upsert's id dedup absorbs the overlap.

Writes but does not commit - the caller owns the transaction.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.encryption import decrypt_secret, encrypt_secret
from app.core.time import utcnow
from app.services.finance.adapters.providers.connections.common import (
    _SIMPLEFIN_ACCESS_CONTEXT,
    ProviderAdapter,
    SyncResult,
    _recompute_net_worth,
    finished,
)
from app.services.finance.adapters.providers.connections.simplefin_sync.mapping import (
    bank_names,
    bridge_errors,
    simplefin_accounts,
    simplefin_transactions,
)
from app.services.finance.adapters.providers.connections.upserts import (
    apply_transactions,
    upsert_accounts,
)
from app.services.finance.adapters.providers.simplefin import SimpleFINClient
from app.services.finance.constants import Provider
from app.services.finance.models import FinanceConnection
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date

# The Bridge serves 90 days a request (today included), and asks for a
# few days of overlap between pulls so a row posting late is not missed.
_WINDOW_DAYS = 89
_OVERLAP_DAYS = 5


def _window(connection: FinanceConnection, today: date) -> date:
    earliest = today - timedelta(days=_WINDOW_DAYS)
    if not connection.sync_cursor:
        return earliest
    last = date.fromisoformat(connection.sync_cursor)
    return max(last - timedelta(days=_OVERLAP_DAYS), earliest)


async def sync_simplefin_connection(
    db: AsyncSession,
    connection: FinanceConnection,
    *,
    client: SimpleFINClient | None = None,
) -> SyncResult:
    """Read every account and the window's transactions, in one request."""
    started = utcnow()
    client = client or SimpleFINClient()
    service = FinanceService(db)
    result = SyncResult(connection_id=connection.id)
    connection.last_sync_attempt_at = utcnow()
    access_url = decrypt_secret(
        connection.access_token_encrypted, context=_SIMPLEFIN_ACCESS_CONTEXT
    )
    today = current_date()
    payload = await client.accounts(
        access_url, start=_window(connection, today), end=today
    )
    connection.label = connection.label or bank_names(payload) or "SimpleFIN"

    account_by_id = await upsert_accounts(
        db, service, connection, Provider.SIMPLEFIN, simplefin_accounts(payload)
    )
    result.accounts = len(account_by_id)
    result.added, result.updated = await apply_transactions(
        db,
        service,
        simplefin_transactions(payload),
        account_by_id,
        connection=connection,
        source=Provider.SIMPLEFIN,
    )
    connection.sync_cursor = today.isoformat()
    finished(db, connection, started=started, result=result)
    # What the Bridge reports wrong is for the user: a bank that needs them
    # says so on the connection, while the banks that worked still synced.
    if errors := bridge_errors(payload):
        connection.status = "error"
        connection.status_detail = "; ".join(errors)[:500]
    db.add(connection)
    await db.flush()
    return result


async def connect_simplefin(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    setup_token: str,
    client: SimpleFINClient | None = None,
) -> SyncResult:
    """Claim the setup token (it works once), keep the access it returns
    encrypted on a new connection, and sync it."""
    client = client or SimpleFINClient()
    access_url = await client.claim(setup_token.strip())
    connection = FinanceConnection(
        owner_user_id=owner_user_id,
        provider=Provider.SIMPLEFIN,
        connection_type="aggregator_token",
        environment="production",
        access_token_encrypted=encrypt_secret(
            access_url, context=_SIMPLEFIN_ACCESS_CONTEXT
        ),
        status="loading",
    )
    db.add(connection)
    await db.flush()
    result = await sync_simplefin_connection(db, connection, client=client)
    await _recompute_net_worth(db, owner_user_id, [result])
    return result


async def _sync(
    db: AsyncSession, connection: FinanceConnection, client: SimpleFINClient
) -> SyncResult:
    return await sync_simplefin_connection(db, connection, client=client)


# No remote revoke: SimpleFIN access is disabled by the user, at SimpleFIN.
ADAPTER = ProviderAdapter(
    provider=Provider.SIMPLEFIN,
    new_client=SimpleFINClient,
    sync=_sync,
    ready=lambda connection: connection.access_token_encrypted is not None,
)
