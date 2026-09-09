"""Settings: connections, categories, payees, and the comms job.

Four sibling routes under one sub-nav, the shape Review already uses.
Connecting a provider is two posts (pattern 1): the first starts a
session and hands back the provider's own page plus an element that
polls the second until the connection lands, so the browser waits
without a line of JavaScript. The two tables are the shared table macro;
Comms shows what the scheduled bill email would say, and never sends it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
)
from starlette.responses import Response

from app.components.backend.api.finance.categories import list_categories
from app.components.backend.api.finance.connections import (
    disconnect_connection,
    list_connections,
    plaid_hosted_link,
    plaid_hosted_link_complete,
    snaptrade_connect,
    snaptrade_connect_complete,
)
from app.components.backend.api.finance.payees import (
    list_merchants,
    merchant_category_summary,
)
from app.components.web_frontend import ranges
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    render,
    templates,
    with_toast,
)
from app.core.config import settings
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.planning.recurring.forecast import upcoming_outflows
from app.services.finance.schemas import HostedLinkCompleteRequest
from app.services.finance.service import FinanceService

SECTION = section("settings")
router = APIRouter(prefix=SECTION.path)

TABS: tuple[tuple[str, str, str], ...] = (
    ("connections", "Connections", ""),
    ("categories", "Categories", "/categories"),
    ("payees", "Payees", "/payees"),
    ("comms", "Comms", "/comms"),
)
CATEGORY_COLUMNS = [
    {"key": "name", "label": "Name"},
    {"key": "classification", "label": "Kind"},
    {
        "key": "transaction_count",
        "label": "Transactions",
        "kind": "int",
        "align": "right",
    },
    {
        "key": "total",
        "label": "Total",
        "kind": "money",
        "align": "right",
        "toned": True,
    },
    {"key": "last_used", "label": "Last used", "kind": "date"},
]
PAYEE_COLUMNS = [
    {"key": "name", "label": "Name", "kind": "avatar"},
    {
        "key": "transaction_count",
        "label": "Transactions",
        "kind": "int",
        "align": "right",
    },
    {
        "key": "total_amount",
        "label": "Total",
        "kind": "money",
        "align": "right",
        "toned": True,
    },
    {"key": "last_date", "label": "Last seen", "kind": "date"},
]
# A connection's stored status, as the card says it.
STATUS = {
    "healthy": ("Connected", "ok"),
    "error": ("Needs attention", "error"),
    "revoked": ("Disconnected", "muted"),
}


# --- the providers ---------------------------------------------------------


async def _plaid_start(
    service: FinanceService, owner_user_id: int | None
) -> dict[str, str]:
    started = await plaid_hosted_link(owner_user_id=owner_user_id)
    return {"url": started.hosted_link_url, "token": started.link_token}


async def _plaid_complete(
    service: FinanceService, owner_user_id: int | None, token: str
) -> dict[str, int]:
    done = await plaid_hosted_link_complete(
        HostedLinkCompleteRequest(link_token=token),
        service=service,
        owner_user_id=owner_user_id,
    )
    return {
        "connections": done.connections,
        "added": sum(r.added for r in done.results),
    }


async def _snaptrade_start(
    service: FinanceService, owner_user_id: int | None
) -> dict[str, str]:
    started = await snaptrade_connect(service=service, owner_user_id=owner_user_id)
    return {"url": started.redirect_uri, "token": str(started.connection_id)}


async def _snaptrade_complete(
    service: FinanceService, owner_user_id: int | None, token: str
) -> dict[str, int]:
    done = await snaptrade_connect_complete(
        service=service, owner_user_id=owner_user_id
    )
    return {
        "connections": done.connections,
        "added": sum(r.added + r.holdings for r in done.results),
    }


@dataclass
class Provider:
    """One connect flow: what it is called, whether the stack has it, what
    it needs configured, and the two halves of its handshake."""

    key: str
    label: str
    what: str
    flag: str
    credentials: tuple[str, ...]
    start: Callable[..., Awaitable[dict[str, str]]]
    complete: Callable[..., Awaitable[dict[str, int]]] = field(repr=False)

    @property
    def built_in(self) -> bool:
        return bool(getattr(settings, self.flag, False))

    @property
    def configured(self) -> bool:
        return all(getattr(settings, name, None) for name in self.credentials)


PROVIDERS: dict[str, Provider] = {
    "plaid": Provider(
        key="plaid",
        label="Plaid",
        what="Connect a bank",
        flag="FINANCE_PLAID",
        credentials=("PLAID_CLIENT_ID", "PLAID_SECRET"),
        start=_plaid_start,
        complete=_plaid_complete,
    ),
    "snaptrade": Provider(
        key="snaptrade",
        label="SnapTrade",
        what="Connect a brokerage",
        flag="FINANCE_SNAPTRADE",
        credentials=("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY"),
        start=_snaptrade_start,
        complete=_snaptrade_complete,
    ),
}


# How long the dialog waits before it stops asking: the provider session
# is the user's to finish, and a tab left open must not poll forever.
POLL_SECONDS = 3
POLL_LIMIT = 60


def _refused(request: Request, provider: Provider, exc: HTTPException) -> Response:
    """A provider said no. The reason is theirs, so it is shown as-is and
    the polling stops."""
    return dialog(
        request,
        "partials/settings/connecting.html",
        422,
        provider=provider,
        errors=[str(exc.detail)],
        started=None,
    )


def _provider(key: str) -> Provider:
    provider = PROVIDERS.get(key)
    if provider is None or not provider.built_in:
        raise HTTPException(status_code=404)
    return provider


# --- the sub-nav -----------------------------------------------------------


def nav_context(current: str) -> dict[str, Any]:
    return {
        "nav_id": "settings-nav",
        "nav_label": "Settings sections",
        "sub_nav": [
            {"key": key, "label": label, "href": SECTION.path + suffix, "count": 0}
            for key, label, suffix in TABS
        ],
        "current_tab": current,
    }


# --- connections -----------------------------------------------------------


def _card(connection: Any) -> dict[str, Any]:
    label, tone = STATUS.get(connection.status, (connection.status.title(), "warn"))
    return {
        "id": connection.id,
        "provider": connection.provider.title(),
        "label": connection.label or connection.provider.title(),
        "environment": connection.environment,
        "status": {"label": label, "tone": tone},
        "detail": connection.status_detail,
        "last_sync": connection.last_successful_sync_at,
    }


async def connections_context(
    service: FinanceService, owner_user_id: int | None
) -> dict[str, Any]:
    listing = await list_connections(service=service, owner_user_id=owner_user_id)
    offered = [p for p in PROVIDERS.values() if p.built_in and p.configured]
    # A provider that is built in but unconfigured offers nothing to click
    # (there is no session to start) and says what to set instead, so a
    # fresh project can see the front door rather than wonder where it went.
    missing = [
        {"label": p.label, "settings": ", ".join(p.credentials)}
        for p in PROVIDERS.values()
        if p.built_in and not p.configured
    ]
    return {
        "cards": [_card(c) for c in listing.items],
        "providers": offered,
        "missing": missing,
        "path": SECTION.path,
    }


@router.get("", include_in_schema=False)
async def connections(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    context = await connections_context(service, owner_user_id)
    return render(
        request,
        "pages/settings/connections.html",
        {"section": SECTION, **nav_context("connections"), **context},
    )


async def _connections_list(
    request: Request, service: FinanceService, owner_user_id: int | None
) -> str:
    """The card list as an out-of-band swap, for a finished connect."""
    context = await connections_context(service, owner_user_id)
    template = templates.get_template("partials/settings/connections.html")
    return template.render(request=request, list_oob=True, **context)


@router.post("/connect/{key}", include_in_schema=False)
async def connect(
    request: Request,
    key: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Step one: start the session and hand back the provider's own page
    plus the element that polls step two."""
    provider = _provider(key)
    try:
        started = await provider.start(service=service, owner_user_id=owner_user_id)
    except HTTPException as exc:  # the provider's own refusal, as itself
        return _refused(request, provider, exc)
    return dialog(
        request,
        "partials/settings/connecting.html",
        provider=provider,
        started=started,
        errors=[],
    )


@router.post("/connect/{key}/complete", include_in_schema=False)
async def connect_complete(
    request: Request,
    key: str,
    token: Annotated[str, Form()] = "",
    url: Annotated[str, Form()] = "",
    attempt: Annotated[int, Form()] = 1,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Step two, asked again every few seconds until the provider says a
    connection landed. Still pending answers with the poller; done answers
    with the finished panel and the refreshed card list out of band."""
    provider = _provider(key)
    try:
        done = await provider.complete(
            service=service, owner_user_id=owner_user_id, token=token
        )
    except HTTPException as exc:
        return _refused(request, provider, exc)
    if not done.get("connections"):
        # Still theirs to finish: the way in stays on screen (the poll
        # replaces the whole body), and the asking stops eventually.
        return dialog(
            request,
            "partials/settings/connecting.html",
            provider=provider,
            started={"token": token, "url": url},
            attempt=attempt + 1,
            waited=attempt >= POLL_LIMIT,
            errors=[],
        )
    await service.db.commit()
    response = dialog(
        request,
        "partials/settings/connected.html",
        provider=provider,
        done=done,
        connections_html=await _connections_list(request, service, owner_user_id),
    )
    added = done.get("added", 0)
    return with_toast(response, f"{provider.label} connected: {added} rows synced.")


@router.get("/connections/{connection_id:int}/remove", include_in_schema=False)
async def remove_form(
    request: Request,
    connection_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    listing = await list_connections(service=service, owner_user_id=owner_user_id)
    connection = next((c for c in listing.items if c.id == connection_id), None)
    if connection is None:
        raise HTTPException(status_code=404)
    return dialog(
        request,
        "partials/settings/disconnect.html",
        connection=_card(connection),
        path=SECTION.path,
    )


@router.delete("/connections/{connection_id:int}", include_in_schema=False)
async def remove(
    connection_id: int,
    background_tasks: BackgroundTasks,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    # The provider-side revoke is a slow round trip; the API hands it to
    # the background so the card disappears at once.
    await disconnect_connection(
        connection_id,
        background_tasks,
        service=service,
        owner_user_id=owner_user_id,
    )
    await service.db.commit()
    # Back to the list, so the card goes with the connection rather than
    # sitting there offering a button that now 404s.
    return dialog_done(SECTION.path, "Disconnected. Its accounts stay.")


# --- categories and payees --------------------------------------------------


@router.get("/categories", include_in_schema=False)
async def categories(
    request: Request,
    days: int = Query(default=ranges.ALL, ge=1),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    listing = await list_categories(
        days=None if days >= ranges.ALL else days,
        service=service,
        owner_user_id=owner_user_id,
    )
    return render(
        request,
        "pages/settings/categories.html",
        {
            "section": SECTION,
            **nav_context("categories"),
            "rows": listing.items,
            "columns": CATEGORY_COLUMNS,
            "days": days,
            "ranges": ranges.WINDOWS,
            "path": SECTION.path,
        },
    )


@router.get("/payees", include_in_schema=False)
async def payees(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    listing = await list_merchants(
        account_ids=None, service=service, owner_user_id=owner_user_id
    )
    return render(
        request,
        "pages/settings/payees.html",
        {
            "section": SECTION,
            **nav_context("payees"),
            "rows": listing.items,
            "columns": PAYEE_COLUMNS,
            "path": SECTION.path,
        },
    )


@router.get("/payees/{merchant_id:int}/categories", include_in_schema=False)
async def payee_categories(
    request: Request,
    merchant_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """How this payee's transactions are filed today: a payee arguing with
    itself shows up here as two rows."""
    # One name, not the whole directory: listing it also aggregates usage
    # and schedules icon fetches, which a single dialog has no use for.
    names = await service.merchant_names({merchant_id})
    if merchant_id not in names:
        raise HTTPException(status_code=404)
    summary = await merchant_category_summary(
        merchant_id, service=service, owner_user_id=owner_user_id
    )
    return dialog(
        request,
        "partials/settings/payee_categories.html",
        name=names[merchant_id],
        summary=summary,
    )


# --- comms ------------------------------------------------------------------


async def bill_email(
    service: FinanceService, owner_user_id: int | None
) -> dict[str, Any]:
    """What tomorrow's bill email would say. The same window and shaping
    the scheduled job uses, so the preview cannot drift from the mail."""
    days = settings.FINANCE_BILL_EMAIL_DAYS
    projection = await service.project_balances(owner_user_id=owner_user_id, days=days)
    bills = upcoming_outflows(projection)
    return {
        "to": settings.FINANCE_BILL_EMAIL_TO,
        "days": days,
        "bills": bills,
        "total": sum(b["amount"] for b in bills),
        "subject": f"{len(bills)} bill{'s' if len(bills) != 1 else ''} due in the next {days} days",
    }


@router.get("/comms", include_in_schema=False)
async def comms(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    email = await bill_email(service, owner_user_id)
    return render(
        request,
        "pages/settings/comms.html",
        {"section": SECTION, **nav_context("comms"), "email": email},
    )
