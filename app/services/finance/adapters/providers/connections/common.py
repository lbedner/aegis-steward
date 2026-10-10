"""What every provider needs: the connection reads and writes, the
adapter and the sync result shape.

A connection row is provider-agnostic - it is a link, an owner, and an
encrypted credential - so reading one back never needs to know which
aggregator issued it. ``SyncResult`` is the tally every provider's sync
pass reports in, which is what lets ``registry`` sum a mixed batch
without asking who produced each row.

Deliberately free of any provider client, so each provider's sync
package imports it without importing another.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import InvalidOperation
import logging
from typing import Any

from cryptography.fernet import InvalidToken
import httpx
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.encryption import decrypt_secret, encrypt_secret
from app.core.time import utcnow
from app.services.finance.adapters.providers import queries
from app.services.finance.adapters.providers.errors import ProviderError
from app.services.finance.constants import (
    CREDENTIAL_CONTEXTS,
    PROVIDER_LABELS,
    Provider,
    sync_source,
)
from app.services.finance.models import FinanceConnection
from app.services.finance.utils import to_cents
from app.services.shared.queries import stored_owner

logger = logging.getLogger(__name__)

# The named slot a connection's encrypted credential occupies. Each provider
# stores a different secret (Plaid an access token, SnapTrade a user secret,
# SimpleFIN an access URL), but all ride the same column, and the context
# string is what keeps one from ever being decrypted as another.
_ACCESS_TOKEN_CONTEXT = CREDENTIAL_CONTEXTS[Provider.PLAID]
_SNAPTRADE_SECRET_CONTEXT = CREDENTIAL_CONTEXTS[Provider.SNAPTRADE]
_SIMPLEFIN_ACCESS_CONTEXT = CREDENTIAL_CONTEXTS[Provider.SIMPLEFIN]


def record_run(
    db: AsyncSession,
    *,
    connection_id: int | None,
    owner_user_id: int | None,
    provider: str,
    started: datetime,
    result: SyncResult | None,
    error: str | None = None,
) -> None:
    """One row per ingestion run, in the table that holds every other one.

    ``finance_import_batch`` has said since it was written that it holds
    "a Plaid/SnapTrade sync pass" - it was simply never given one. So a
    sync and an uploaded file now answer the same question in the same
    place: what came in, from where, and what did it bring.

    Takes plain values, never the connection row. On the failure path the
    savepoint has just rolled back, and touching a mapped attribute there
    can go to the database - which would make the bookkeeping raise and
    MASK the error it was recording.
    """
    from app.services.finance.models.imports import FinanceImportBatch

    tally = result.model_dump(exclude={"connection_id"}) if result else {}
    db.add(
        FinanceImportBatch(
            # 0 for "nobody in particular", the convention the CSV import
            # already uses: the column is NOT NULL, and a single-user
            # install has no owner to name. Caught live rather than in a
            # test, because every test names owner 1.
            owner_user_id=stored_owner(owner_user_id),
            connection_id=connection_id,
            source_type=sync_source(provider),
            status="committed" if result else "failed",
            error=error,
            rows_total=tally.get("added", 0) + tally.get("updated", 0),
            rows_inserted=tally.get("added", 0),
            rows_updated=tally.get("updated", 0),
            detail=tally or None,
            started_at=started,
            finished_at=utcnow(),
        )
    )


def finished(
    db: AsyncSession,
    connection: FinanceConnection,
    *,
    started: datetime,
    result: SyncResult,
) -> None:
    """A sync that worked: the connection is fine, and the run is on the
    record. One call, because a pass that updates one without the other
    is a connection claiming to be current with nothing behind it."""
    mark_healthy(connection)
    record_run(
        db,
        connection_id=connection.id,
        owner_user_id=connection.owner_user_id,
        provider=str(connection.provider),
        started=started,
        result=result,
    )


def mark_healthy(connection: FinanceConnection) -> None:
    """A connection that just synced is fine, and says nothing else.

    Clearing the error is the half that was missing: the failure path
    writes ``status_detail`` and ``last_error_code``, nothing cleared
    them, so a connection that recovered still carried the sentence that
    broke it. A card read "healthy" over "missing_credentials: ... are
    not configured" for as long as it had once been true.
    """
    connection.status = "healthy"
    connection.needs_user_action = False
    connection.status_detail = None
    connection.last_error_code = None
    connection.last_successful_sync_at = utcnow()


def _to_cents(amount: float | str | None) -> int | None:
    """A provider's amount in cents, rounded as every amount here is
    (``to_cents``: half up, #215); one that is not a number is None."""
    try:
        return None if amount is None else to_cents(amount)
    except InvalidOperation:
        return None


async def new_connection(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    provider: str,
    credential: str,
    environment: str = "production",
    connection_type: str = "aggregator_token",
    status: str = "loading",
    **fields: Any,
) -> FinanceConnection:
    """A connection row holding its credential encrypted under the
    provider's context: by default waiting (``loading``) for its first
    sync. ``fields`` are any other columns the provider knows up front."""
    connection = FinanceConnection(
        owner_user_id=owner_user_id,
        provider=provider,
        connection_type=connection_type,
        environment=environment,
        access_token_encrypted=encrypt_secret(
            credential, context=CREDENTIAL_CONTEXTS[provider]
        ),
        status=status,
        **fields,
    )
    db.add(connection)
    await db.flush()
    return connection


def since_last_pull(
    connection: FinanceConnection, today: date, *, lookback: int, overlap: int
) -> date:
    """Where a provider with no cursor re-reads from: the last pull (an ISO
    date in ``sync_cursor``) less ``overlap`` days, so a row posting late is
    not missed, and never further back than ``lookback`` days."""
    earliest = today - timedelta(days=lookback)
    if not connection.sync_cursor:
        return earliest
    last = date.fromisoformat(connection.sync_cursor)
    return max(last - timedelta(days=overlap), earliest)


class SyncResult(BaseModel):
    """What one connection's sync pass wrote."""

    connection_id: int
    accounts: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    holdings: int = 0
    trades: int = 0


async def list_provider_connections(
    db: AsyncSession,
    *,
    provider: str | None = None,
    owner_user_id: int | None = None,
) -> list[FinanceConnection]:
    """Active (non-deleted) provider connections for an owner, optionally
    narrowed to one provider."""
    return await queries.connections_for_owner(
        db, provider=provider, owner_user_id=owner_user_id
    )


async def list_plaid_connections(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> list[FinanceConnection]:
    """Active (non-deleted) Plaid connections for an owner."""
    return await list_provider_connections(
        db, provider=Provider.PLAID, owner_user_id=owner_user_id
    )


async def get_connection(
    db: AsyncSession, connection_id: int, *, owner_user_id: int | None = None
) -> FinanceConnection | None:
    """A single non-deleted connection, scoped to the owner when given."""
    return await queries.connection_by_id_live(
        db, connection_id, owner_user_id=owner_user_id
    )


async def _recompute_net_worth(
    db: AsyncSession, owner_user_id: int | None, results: list[SyncResult]
) -> None:
    """Post-sync reconcile: pair internal transfers (so a card payment doesn't
    double-count as spend), detect recurring streams + "wasting money" insights,
    then refresh the net-worth snapshot series so the Overview trend reflects
    the new data. No-op if nothing synced."""
    if not results:
        return
    from app.services.finance.domains.detection import (
        detect_recurring,
        detect_transfers,
        generate_insights,
    )
    from app.services.finance.domains.ledger import networth

    await detect_transfers(db, owner_user_id=owner_user_id)
    await detect_recurring(db, owner_user_id=owner_user_id)
    await generate_insights(db, owner_user_id=owner_user_id)
    await networth.recompute_snapshots(db, owner_user_id=owner_user_id)


Revoke = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class ProviderAdapter:
    """What the registry needs from one aggregator, so it dispatches on a
    table instead of an ``if`` per provider: a new provider is one more
    adapter, and no verb learns its name.

    ``sync`` and ``revoke`` take the client (or a factory for it) so tests
    inject fakes and a deployment never builds a client it has no rows for.
    """

    provider: str
    new_client: Callable[[], Any]
    # ``sync(db, connection, client=...)``: the provider's own sync pass.
    sync: Callable[..., Awaitable[SyncResult]]
    # Whether a row can sync at all (SnapTrade's portal-pending rows cannot).
    ready: Callable[[FinanceConnection], bool] = lambda _connection: True
    # The best-effort remote revoke for a disconnect, built from the row
    # before the local teardown clears its credential; None when there is
    # nothing to revoke.
    revoke: Callable[[FinanceConnection, Callable[[], Any]], Revoke | None] = (
        lambda _connection, _client: None
    )


def stored_credential(connection: FinanceConnection, context: str) -> str | None:
    """The connection's decrypted credential, or None when it has none or
    it cannot be decrypted (a corrupted or rekeyed ciphertext must never
    block a local teardown: there is simply nothing usable to revoke)."""
    if not connection.access_token_encrypted:
        return None
    try:
        return decrypt_secret(connection.access_token_encrypted, context=context)
    except InvalidToken as exc:
        logger.warning(
            "Stored credential for connection %s is undecryptable; "
            "skipping provider revoke: %s",
            connection.id,
            exc,
        )
        return None


def best_effort(
    connection: FinanceConnection, call: Callable[[], Awaitable[Any]]
) -> Revoke:
    """A revoke that never raises: the local teardown already happened, so
    an already-invalid credential or an unreachable API is only logged."""

    async def revoke() -> None:
        try:
            await call()
        except (ProviderError, httpx.HTTPError) as exc:
            logger.warning(
                "%s revoke failed for connection %s (already torn down locally): %s",
                PROVIDER_LABELS[connection.provider],
                connection.id,
                exc,
            )

    return revoke
