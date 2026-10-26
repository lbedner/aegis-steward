"""A bank that needs you shows on every page (#310).

Health used to show only in Settings, in raw words. Every page now loads
a banner (``layouts/app_shell``) that lists each link that needs you -
one the bank stopped (a sign-in, an expiry, an error) or one gone stale
(no sync in a week) - in words, with Reconnect where the bank can be
reconnected in place: Plaid's update mode, which keeps the connection,
its accounts and their history, or SimpleFIN Bridge's account page,
where the Bridge keeps each bank's sign-in. Done hands the next sync to
the worker; a sync that succeeds is what clears the banner.

Its own module because Settings is at its budget.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.components.web_frontend.filters import freshness
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    render,
    where_from,
)
from app.components.web_frontend.routes.finance.connect_providers import (
    SIMPLEFIN_ACCOUNT,
)
from app.core.config import settings
from app.services.finance.constants import (
    CONNECTION_NEEDS_YOU,
    PROVIDER_LABELS,
    Provider,
    connection_status,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.schemas import ConnectionResponse
from app.services.finance.service import FinanceService, arrivals, connections, placing

SECTION = section("settings")
router = APIRouter(prefix=SECTION.path)


# How the Bridge reports a bank it lost touch with: "Connection to Optum
# Bank HSA may need attention. Auth required" (a sign-in), or with no
# reason, or another one.
_BRIDGE_SAID = re.compile(
    r"Connection to (?P<bank>.+?) may need attention\.?\s*(?P<why>.*)", re.I
)


def readable(detail: str) -> str:
    """What a bank said, in plain words where the Bridge's shape is known
    ("Optum Bank HSA needs you to sign in again"); anything else as said."""

    def plain(said: str) -> str:
        found = _BRIDGE_SAID.fullmatch(said.strip())
        if found is None:
            return said.strip()
        bank, why = found["bank"].strip(), found["why"].rstrip(".")
        if why.lower() == "auth required":
            return f"{bank} needs you to sign in again"
        return f"{bank} needs attention" + (f": {why}" if why else "")

    return "; ".join(plain(said) for said in detail.split(";"))


def needs_you(connection: Any) -> str | None:
    """What a link needs you for, in words - or None when it is fine.
    Stale is a week without a successful sync, the same week the account
    header turns amber for (``freshness``); never synced is unknown, not
    stale."""
    if connection.status in CONNECTION_NEEDS_YOU:
        # What the bank said, when it said anything: "Needs attention"
        # alone hid an HSA out for ten days (#532).
        if connection.status_detail:
            return readable(connection.status_detail)
        return connection_status(connection.status)[0]
    if connection.new_accounts:
        return arrivals.WORDS
    # Stale only where this stack syncs: a dev stack never schedules the
    # bank sync (``server_only``, #532) and leaves it to the server, so
    # every link there reads a week old by design (#584).
    if (
        connection.status == "healthy"
        and connection.last_successful_sync_at
        and not settings.is_dev
    ):
        last = freshness(connection.last_successful_sync_at, "sync")
        if last["tone"] != "ok":
            return f"Last synced {last['label']}"
    return None


# Where a sign-in is mended: Plaid's update page for one bank, or the
# Bridge, which keeps every bank's sign-in for a SimpleFIN link.
_SIGNS_IN = frozenset({Provider.PLAID, Provider.SIMPLEFIN})


def reconnectable(connection: Any) -> bool:
    """Whether Sign in again can mend it: Plaid's update mode, or the
    Bridge's account page."""
    return connection.provider in _SIGNS_IN and needs_you(connection) is not None


def _name(connection: Any) -> str:
    return connection.label or PROVIDER_LABELS.get(
        connection.provider, connection.provider
    )


def _unsaid(name: str, reason: str) -> str | None:
    """The link's name, unless its reason already names one of its banks:
    a link to six banks led the banner with all six before the one that
    needed you (#547)."""
    banks = [bank.strip() for bank in name.split(",")]
    return None if any(bank and bank in reason for bank in banks) else name


async def _needing(
    service: FinanceService, owner_user_id: int | None
) -> list[dict[str, Any]]:
    """Every link that needs you, in words, with whether Sign in again
    mends it - read as the Settings cards read them, so the two never
    disagree."""
    return [
        {
            "id": c.id,
            "name": _unsaid(_name(c), reason),
            "reason": reason,
            "reconnect": reconnectable(c),
        }
        for c in map(
            ConnectionResponse.from_row,
            await connections.list_provider_connections(
                service.db, owner_user_id=owner_user_id
            ),
        )
        if (reason := needs_you(c))
    ]


@router.get("/connections/attention", include_in_schema=False)
async def attention(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The banner: one line of what needs you, or nothing at all - and
    nothing on Settings > Connections, where the cards say it."""
    on_cards = where_from(request, "") == SECTION.path
    rows = [] if on_cards else await _needing(service, owner_user_id)
    said = "|".join(sorted(f"{r['id']}:{r['reason']}" for r in rows))
    return render(
        request,
        "partials/settings/attention.html",
        {
            "rows": rows,
            "path": SECTION.path,
            "key": hashlib.sha1(said.encode()).hexdigest()[:12],
        },
    )


@router.get("/connections/attention/review", include_in_schema=False)
async def attention_review(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Every link that needs you, a fix each: the banner's Review."""
    return dialog(
        request,
        "partials/settings/attention_review.html",
        rows=await _needing(service, owner_user_id),
        path=SECTION.path,
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
    if connection.provider == Provider.SIMPLEFIN:
        return _reconnecting(
            request,
            connection,
            SIMPLEFIN_ACCOUNT,
            how="SimpleFIN Bridge keeps your banks' sign-ins: sign in to the "
            "bank it flags there, then come back.",
            opens="Open SimpleFIN Bridge",
        )
    how = f"Sign in to {_name(connection)} again on the bank's page."
    session = await connections.relink_connection(
        service.db, connection_id, owner_user_id=owner_user_id
    )
    if session is None:
        return _reconnecting(
            request,
            connection,
            None,
            how=how,
            errors=[
                "This bank can't be reconnected in place. Disconnect it and "
                "connect it again - its accounts and history stay."
            ],
        )
    url, _token = session
    return _reconnecting(request, connection, url, how=how)


def _reconnecting(
    request: Request,
    connection: Any,
    url: str | None,
    *,
    how: str,
    opens: str = "Open the bank's page",
    errors: list[str] | None = None,
) -> Response:
    return dialog(
        request,
        "partials/settings/reconnecting.html",
        422 if errors else 200,
        how=how,
        opens=opens,
        reason=connection.status_detail and readable(connection.status_detail),
        connection_id=connection.id,
        url=url,
        errors=errors or [],
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
