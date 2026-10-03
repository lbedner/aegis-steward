"""The connect flows Settings offers, one ``Provider`` each.

Each is what it is called, whether the stack has it (its flag), what it
needs configured, and the two halves of its handshake; ``settings.py``
renders whichever are on and dispatches on this table, so a new
aggregator is one more entry here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.components.backend.api.finance.connections import (
    plaid_hosted_link,
    plaid_hosted_link_complete,
    simplefin_connect,
    snaptrade_connect,
    snaptrade_connect_complete,
)
from app.core.config import settings
from app.services.finance.adapters.providers.simplefin import DEMO_PAGE
from app.services.finance.constants import PROVIDER_LABELS
from app.services.finance.schemas import (
    HostedLinkCompleteRequest,
    SimpleFINConnectRequest,
)
from app.services.finance.service import FinanceService


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


async def _simplefin_start(
    service: FinanceService, owner_user_id: int | None
) -> dict[str, str]:
    return {"url": SIMPLEFIN_CREATE, "token": ""}


async def _simplefin_complete(
    service: FinanceService, owner_user_id: int | None, token: str
) -> dict[str, int]:
    done = await simplefin_connect(
        SimpleFINConnectRequest(setup_token=token),
        service=service,
        owner_user_id=owner_user_id,
    )
    return {"connections": 1, "added": done.added}


# Where a SimpleFIN user links banks and makes the setup token.
SIMPLEFIN_CREATE = "https://bridge.simplefin.org/simplefin/create"


@dataclass
class Provider:
    """One connect flow: what it is called, whether the stack has it, what
    it needs configured, and the two halves of its handshake.

    ``brings_token``: the user finishes on the provider's page and pastes
    back a token it gives them (SimpleFIN), rather than this dialog
    waiting for the provider to report a connection."""

    key: str
    what: str
    flag: str
    credentials: tuple[str, ...]
    start: Callable[..., Awaitable[dict[str, str]]]
    complete: Callable[..., Awaitable[dict[str, int]]] = field(repr=False)
    brings_token: bool = False
    # Where to try it with fake data first, when the provider offers that.
    demo_url: str | None = None

    @property
    def label(self) -> str:
        return PROVIDER_LABELS[self.key]

    @property
    def built_in(self) -> bool:
        return bool(getattr(settings, self.flag, False))

    @property
    def configured(self) -> bool:
        return all(getattr(settings, name, None) for name in self.credentials)


PROVIDERS: dict[str, Provider] = {
    "plaid": Provider(
        key="plaid",
        what="Connect a bank",
        flag="FINANCE_PLAID",
        credentials=("PLAID_CLIENT_ID", "PLAID_SECRET"),
        start=_plaid_start,
        complete=_plaid_complete,
    ),
    "snaptrade": Provider(
        key="snaptrade",
        what="Connect a brokerage",
        flag="FINANCE_SNAPTRADE",
        credentials=("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY"),
        start=_snaptrade_start,
        complete=_snaptrade_complete,
    ),
    "simplefin": Provider(
        key="simplefin",
        what="Connect banks through SimpleFIN",
        flag="FINANCE_SIMPLEFIN",
        credentials=(),
        start=_simplefin_start,
        complete=_simplefin_complete,
        brings_token=True,
        demo_url=DEMO_PAGE,
    ),
}
