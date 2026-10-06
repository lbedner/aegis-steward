"""A bank that needs you shows on every page (#310).

Health used to show only in Settings, in raw words. Every page now loads
a banner (``layouts/app_shell``) that lists each link that needs you -
one the bank stopped (a sign-in, an expiry, an error) or one gone stale
(no sync in a week) - in words, with Reconnect where the bank can be
reconnected in place: Plaid's update mode, which keeps the connection,
its accounts and their history. Done hands the next sync to the worker;
a sync that succeeds is what clears the banner.

Its own module because Settings is at its budget.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.components.web_frontend.filters import freshness
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, dialog_done, or_404, render
from app.services.finance.adapters.providers import connections
from app.services.finance.adapters.providers.connections import arrivals, placing
from app.services.finance.constants import (
    CONNECTION_NEEDS_YOU,
    PROVIDER_LABELS,
    Provider,
    connection_status,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.schemas import ConnectionResponse
from app.services.finance.service import FinanceService

SECTION = section("settings")
router = APIRouter(prefix=SECTION.path)


def needs_you(connection: Any) -> str | None:
    """What a link needs you for, in words - or None when it is fine.
    Stale is a week without a successful sync, the same week the account
    header turns amber for (``freshness``); never synced is unknown, not
    stale."""
    if connection.status in CONNECTION_NEEDS_YOU:
        return connection_status(connection.status)[0]
    if connection.new_accounts:
        return arrivals.WORDS
    if connection.status == "healthy" and connection.last_successful_sync_at:
        last = freshness(connection.last_successful_sync_at, "sync")
        if last["tone"] != "ok":
            return f"Last synced {last['label']}"
    return None


def reconnectable(connection: Any) -> bool:
    """Whether Reconnect can mend it in place: Plaid's update mode."""
    return connection.provider == Provider.PLAID and needs_you(connection) is not None


def _name(connection: Any) -> str:
    return connection.label or PROVIDER_LABELS.get(
        connection.provider, connection.provider
    )


@router.get("/connections/attention", include_in_schema=False)
async def attention(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The banner: every link that needs you, or nothing at all."""
    rows = [
        {
            "id": c.id,
            "name": _name(c),
            "reason": reason,
            "reconnect": reconnectable(c),
        }
        # As the Settings cards read them, so the two never disagree.
        for c in map(
            ConnectionResponse.from_row,
            await connections.list_provider_connections(
                service.db, owner_user_id=owner_user_id
            ),
        )
        if (reason := needs_you(c))
    ]
    return render(
        request,
        "partials/settings/attention.html",
        {"rows": rows, "path": SECTION.path},
    )


async def _connection(
    service: FinanceService, owner_user_id: int | None, connection_id: int
) -> Any:
    return or_404(
        await connections.get_connection(
            service.db, connection_id, owner_user_id=owner_user_id
        )
    )


@router.post("/connections/{connection_id:int}/reconnect", include_in_schema=False)
async def reconnect(
    request: Request,
    connection_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Start the bank's own update page: the sign-in is the bank's to take,
    in its tab; Done brings you back."""
    connection = await _connection(service, owner_user_id, connection_id)
    session = await connections.relink_connection(
        service.db, connection_id, owner_user_id=owner_user_id
    )
    if session is None:
        return dialog(
            request,
            "partials/settings/reconnecting.html",
            422,
            name=_name(connection),
            connection_id=connection_id,
            url=None,
            errors=[
                "This bank can't be reconnected in place. Disconnect it and "
                "connect it again - its accounts and history stay."
            ],
            path=SECTION.path,
        )
    url, _token = session
    return dialog(
        request,
        "partials/settings/reconnecting.html",
        name=_name(connection),
        connection_id=connection_id,
        url=url,
        errors=[],
        path=SECTION.path,
    )


@router.post("/connections/{connection_id:int}/reconnect/done", include_in_schema=False)
async def reconnect_done(
    connection_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Ask the bank again, on the worker: a sync that succeeds is what
    returns the link to healthy and clears the banner."""
    connection = await _connection(service, owner_user_id, connection_id)
    if arrivals.waiting_to_add(connection):
        # Plaid's page has been seen; what was added arrives with the sync.
        arrivals.offer(connection, False)
        service.db.add(connection)
        await service.db.commit()
    await placing.sync_soon(connection)
    return dialog_done(
        SECTION.path,
        f"Checking {_name(connection)} again; the banner clears once it syncs.",
    )
