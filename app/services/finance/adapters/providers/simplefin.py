"""The SimpleFIN Bridge client: claim a setup token, read accounts.

SimpleFIN is what open-source budgeting apps use for US banks. The user
connects banks on the Bridge's site and gets a one-time setup token: a
base64 claim URL. POSTing to it once returns an access URL with Basic-auth
credentials in it - the read-only credential this app keeps (encrypted).
``GET {access}/accounts`` returns every linked account with its balance
and transactions for a date window.

The Bridge budgets about 24 requests a day; its windowing rules live with
the sync that follows them (``simplefin_sync.sync``). Its ``errlist`` is
for the user to see, bar ``gen.api``.
Protocol: https://www.simplefin.org/protocol.html
"""

from __future__ import annotations

import base64
import binascii
import calendar
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.services.finance.adapters.providers.errors import ProviderError

_TIMEOUT_SECONDS = 30.0
# The Bridge's developer page: a fresh demo token on every visit, for fixed
# fake data, no sign-up. Its demo access lives on the beta host.
DEMO_PAGE = "https://beta-bridge.simplefin.org/info/developers"
# The demo token's access signs in as this (``demo:demo``).
_DEMO_USER = "demo"


class SimpleFINError(ProviderError):
    """A SimpleFIN Bridge error."""


def _unix(day: date) -> int:
    """Midnight UTC of ``day``, as the Bridge takes dates."""
    return calendar.timegm(day.timetuple())


def _credentialed(access_url: str) -> tuple[str, tuple[str, str]]:
    """The access URL without its credentials, and the credentials as
    Basic auth: they ride the header, never the logged URL."""
    parts = urlsplit(access_url)
    if parts.scheme != "https" or not parts.username:
        raise SimpleFINError(
            "invalid_access", "The stored SimpleFIN access is not usable."
        )
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    base = urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
    return base, (parts.username, parts.password or "")


def is_demo(access_url: str) -> bool:
    """An access URL from the demo token: it signs in as ``demo``. Not its
    host - real tokens come from the beta host too now (#400)."""
    return urlsplit(access_url).username == _DEMO_USER


def _raise_for(response: httpx.Response, forbidden: str) -> None:
    if response.status_code == 403:
        raise SimpleFINError("403", forbidden)
    if response.status_code >= 400:
        raise SimpleFINError(str(response.status_code), response.text[:200])


class SimpleFINClient:
    """``transport`` lets tests answer for the Bridge."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT_SECONDS)

    async def claim(self, setup_token: str) -> str:
        """Trade a setup token for the access URL. A token works once."""
        try:
            claim_url = base64.b64decode(setup_token, validate=True).decode()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            claim_url = ""
        if not claim_url.startswith("https://"):
            raise SimpleFINError(
                "invalid_token", "That is not a SimpleFIN setup token."
            )
        async with self._http() as http:
            response = await http.post(claim_url, headers={"Content-Length": "0"})
        # The protocol's checklist: a refused claim may mean someone else
        # claimed the token first, so the user should disable it.
        _raise_for(
            response,
            "That setup token was already used. If you didn't use it, it may "
            "have been compromised: disable it at SimpleFIN, then make a new one.",
        )
        access_url = response.text.strip()
        _credentialed(access_url)  # refuse anything that is not one
        return access_url

    async def accounts(
        self, access_url: str, *, start: date, end: date
    ) -> dict[str, Any]:
        """Every linked account with its balance and the transactions from
        ``start`` through ``end`` (pending ones too), protocol version 2."""
        base, auth = _credentialed(access_url)
        params = {
            "version": "2",
            "pending": "1",
            "start-date": str(_unix(start)),
            # The window's end is exclusive: midnight after ``end``.
            "end-date": str(_unix(end + timedelta(days=1))),
        }
        async with self._http() as http:
            response = await http.get(f"{base}/accounts", params=params, auth=auth)
        _raise_for(
            response,
            "SimpleFIN no longer accepts this connection's access: it was "
            "disabled there. Connect again with a new setup token.",
        )
        return response.json()
