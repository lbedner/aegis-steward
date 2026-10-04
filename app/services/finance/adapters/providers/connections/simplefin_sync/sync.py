"""One sync pass over a SimpleFIN connection, and connecting one.

One access URL covers every bank the user linked at SimpleFIN, so one
connection row holds it (encrypted) and one request a pass reads them
all. There is no cursor: each pass re-reads from the last one, and the
shared upsert's id dedup absorbs the overlap.

Writes but does not commit - the caller owns the transaction.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.encryption import decrypt_secret
from app.core.time import utcnow
from app.services.finance.adapters.providers.connections.common import (
    _SIMPLEFIN_ACCESS_CONTEXT,
    ProviderAdapter,
    SyncResult,
    _recompute_net_worth,
    finished,
    new_connection,
    since_last_pull,
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
from app.services.finance.adapters.providers.simplefin import (
    SimpleFINClient,
    is_demo,
)
from app.services.finance.constants import PROVIDER_LABELS, Provider
from app.services.finance.models import FinanceConnection
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date

logger = logging.getLogger(__name__)

# The Bridge warns past 45 days a request (today included) and asks for a
# few days of overlap between pulls, so a row posting late is not missed.
# A first sync fills this many requests' worth of history.
_WINDOW_DAYS = 45
_OVERLAP_DAYS = 5
_BACKFILL_WINDOWS = 2


def _windows(connection: FinanceConnection, today: date) -> list[tuple[date, date]]:
    """The (start, end) of each request this pass makes, oldest first: the
    backfill on a first sync, else one window from the last pull."""
    if connection.sync_cursor:
        start = since_last_pull(
            connection, today, lookback=_WINDOW_DAYS - 1, overlap=_OVERLAP_DAYS
        )
        return [(start, today)]
    return [
        (
            today - timedelta(days=_WINDOW_DAYS * (n + 1) - 1),
            today - timedelta(days=_WINDOW_DAYS * n),
        )
        for n in reversed(range(_BACKFILL_WINDOWS))
    ]


def _merged(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """The windows' payloads as one, oldest first: each account once, as
    its newest window reports it (its balance as of now), with every
    window's transactions. An account only in an older window - closed
    since - still has its rows."""
    accounts: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        for account in payload.get("accounts") or []:
            seen = accounts.get(str(account.get("id")), {})
            accounts[str(account.get("id"))] = {
                **account,
                "transactions": [
                    *(seen.get("transactions") or []),
                    *(account.get("transactions") or []),
                ],
            }
    return {**payloads[-1], "accounts": list(accounts.values())}


async def sync_simplefin_connection(
    db: AsyncSession,
    connection: FinanceConnection,
    *,
    client: SimpleFINClient | None = None,
) -> SyncResult:
    """Read every account and the window's transactions: one request, or
    a backfill's few on a first sync."""
    started = utcnow()
    client = client or SimpleFINClient()
    service = FinanceService(db)
    result = SyncResult(connection_id=connection.id)
    connection.last_sync_attempt_at = utcnow()
    access_url = decrypt_secret(
        connection.access_token_encrypted, context=_SIMPLEFIN_ACCESS_CONTEXT
    )
    today = current_date()
    payload = _merged(
        await asyncio.gather(
            *(
                client.accounts(access_url, start=start, end=end)
                for start, end in _windows(connection, today)
            )
        )
    )
    connection.label = (
        connection.label or bank_names(payload) or PROVIDER_LABELS[Provider.SIMPLEFIN]
    )

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
    # What the Bridge reports wrong for the user (a bank to log in to again)
    # says so on the connection, while the banks that worked still synced;
    # what it reports for the developer is logged.
    for_user, for_developer = bridge_errors(payload)
    for message in for_developer:
        logger.warning("SimpleFIN, connection %s: %s", connection.id, message)
    if for_user:
        connection.status = "error"
        connection.status_detail = "; ".join(for_user)[:500]
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
    connection = await new_connection(
        db,
        owner_user_id=owner_user_id,
        provider=Provider.SIMPLEFIN,
        credential=access_url,
        environment="sandbox" if is_demo(access_url) else "production",
    )
    result = await sync_simplefin_connection(db, connection, client=client)
    await _recompute_net_worth(db, owner_user_id, [result])
    return result


# No remote revoke: SimpleFIN access is disabled by the user, at SimpleFIN.
ADAPTER = ProviderAdapter(
    provider=Provider.SIMPLEFIN,
    new_client=SimpleFINClient,
    sync=sync_simplefin_connection,
)
