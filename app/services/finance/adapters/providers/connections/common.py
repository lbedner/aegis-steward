"""What both providers need: the connection reads and the sync result shape.

A connection row is provider-agnostic - it is a link, an owner, and an
encrypted credential - so reading one back never needs to know which
aggregator issued it. ``SyncResult`` is the tally every provider's sync
pass reports in, which is what lets ``registry`` sum a mixed batch
without asking who produced each row.

Deliberately free of any provider client, so ``plaid_sync`` and
``snaptrade_sync`` can both import it without importing each other.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import utcnow
from app.services.finance.adapters.providers import queries
from app.services.finance.constants import Provider
from app.services.finance.models import FinanceConnection

# The named slot a connection's encrypted credential occupies. Each provider
# stores a different secret (Plaid an access token, SnapTrade a user secret),
# but both ride the same column, and the context string is what keeps one
# from ever being decrypted as the other.
_ACCESS_TOKEN_CONTEXT = "finance.plaid.access_token"
_SNAPTRADE_SECRET_CONTEXT = "finance.snaptrade.user_secret"


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
            owner_user_id=0 if owner_user_id is None else owner_user_id,
            connection_id=connection_id,
            source_type=f"{provider}_sync"[:16],
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


def _to_cents(amount: float | None) -> int | None:
    return None if amount is None else round(amount * 100)


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
