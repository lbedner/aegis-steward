"""A payee's brand icon, resolved server-side.

A payee resolves to a KEY: its Plaid logo URL when a connection gave us
one, else the domain of its stored website, else a guessed
``<name>.com``. The key names a stored ``finance_icon`` row. Two ways to
hand the bytes to a client, both off the same resolution:

- ``payee_icons`` (and ``payee_icons_by_name``): an ``Icon`` whose
  ``url`` is a same-origin ``/icons?key=...`` the browser fetches and
  caches once, for the server-rendered pages.
- ``icons_for_names``: the bytes inlined as base64, for the Flet client,
  which cannot fetch them by URL (the upstream service sends no CORS
  header, and Flet resolves a relative ``Image.src`` against its assets
  directory).

The request path NEVER fetches. Resolution reads memory, then the
``finance_icon`` table; domains neither knows are handed to a background
task that fetches upstream and persists what it finds, so the first page
after a genuine miss renders the initial-letter fallback and the next one
has the icon. Before this, a process restart emptied the in-memory cache
and the next render paid ~130 sequential-ish upstream round trips inside
the request (observed: a 946ms overview that spent ~99% on favicons).

A domain that does not resolve is not an error - it is stored as a
NEGATIVE row (NULL bytes) so it is not retried until the row ages out,
and the frontend falls back to the initial-letter avatar, the same way an
unmatched merchant degrades everywhere else in this app.
"""

import asyncio
import base64
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import queries
from app.services.finance.models import FinanceIcon
from app.services.finance.utils import normalize_payee, utcnow

# Below this a "domain" is more likely noise than a brand; above it, the
# string is a bank descriptor rather than a name ("INTEREST CHARGED TO
# PUR PR-11/28/25." -> "interestchargedtopurpr112825.com"), and a fetch
# for it can only ever miss.
_MIN_DOMAIN_LENGTH = 2
_MAX_DOMAIN_LENGTH = 24

UPSTREAM = "https://www.google.com/s2/favicons"
ICON_PATH = "/icons"
# The stored key column's width; a longer logo URL is simply not tried.
_MAX_KEY_LENGTH = 255

# domain -> base64 png, or None for a domain already known to miss. A
# read-through layer over finance_icon rows - it only ever mirrors what
# the table says (or what a fill just stored), so bounded process-local
# state, and nothing user-scoped.
_CACHE: dict[str, str | None] = {}
_CACHE_MAX = 4096
_CONCURRENCY = 24
_TIMEOUT_SECONDS = 4.0

# A stored miss is retried this much later. Brands do gain favicons (a
# payee's website gets set, a new brand launches), just not per-render.
_NEGATIVE_RETRY = timedelta(days=7)

# Domains a background fill is already fetching - a burst of requests over
# the same cold page must not schedule the same upstream fetch N times.
_IN_FLIGHT: set[str] = set()


def domain_from_website(website_url: str | None) -> str | None:
    """The bare host from a stored payee website - the AUTHORITATIVE source
    when a payee has one, because guessing cannot reach it.

    The guess below only ever tries ``<name>.com``, which is wrong in two
    ways a user can trivially fix by typing the real address: it misses
    every other TLD ("aegis-stack.io"), and it strips the punctuation that
    was part of the name ("Aegis Stack" -> "aegisstack", never
    "aegis-stack"). Worse, a plausible-looking ``.com`` may belong to
    somebody else entirely - "aegis-stack.com" resolves to a real, unrelated
    site - so a confident guess can render a stranger's logo on your bill.
    An explicit domain removes all of that.
    """
    raw = (website_url or "").strip()
    if not raw:
        return None
    host = raw.split("//", 1)[-1]  # drop any scheme
    host = host.split("/", 1)[0]  # drop any path
    host = host.split("?", 1)[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    # A bare host has a dot and no spaces; anything else was not a domain.
    return host if ("." in host and " " not in host and len(host) > 3) else None


# A fund's FULL name never guesses to a usable domain the way
# ``merchant_icon_domain`` guesses a payee's - "Vanguard Total Intl Stk Idx
# I" squashed whole is "vanguardtotalintlstkidxi.com", nothing. But the
# ISSUER does, and it's reliably the name's first word ("Vanguard", "Schwab",
# "Fidelity", "iShares", ...), the same generic one-word-guess idea, just
# scoped to where a fund name actually keeps its brand instead of squashing
# the whole descriptive string. Explicit overrides exist only for the
# issuers whose real domain the first word gets wrong (a multi-word name, or
# a sub-brand trading under its parent's site).
_FUND_FAMILY_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("spdr", "ssga.com"),  # SPDR funds are issued by State Street
    ("state street", "ssga.com"),
    ("t. rowe price", "troweprice.com"),
    ("t rowe price", "troweprice.com"),
    ("american funds", "americanfunds.com"),
    ("dodge & cox", "dodgeandcox.com"),
)


def fund_family_domain(security_name: str | None) -> str | None:
    """The issuing fund family's domain - checked overrides first (the
    handful of issuers a first-word guess gets wrong), otherwise the
    generic guess: ``<first word>.com``. AUTHORITATIVE either way, the same
    role ``domain_from_website`` plays for a payee - a fund's descriptive
    suffix ("Total Intl Stk Idx I") is never worth guessing past the brand.
    """
    lowered = (security_name or "").lower()
    for needle, domain in _FUND_FAMILY_OVERRIDES:
        if needle in lowered:
            return domain
    first_word = lowered.split()[0].strip(".,&") if lowered.split() else ""
    if len(first_word) < _MIN_DOMAIN_LENGTH:
        return None
    return f"{first_word}.com"


def merchant_icon_domain(payee_name: str | None) -> str | None:
    """The domain guessed from ``payee_name``, or None when there is
    nothing plausible to guess from."""
    normalized = normalize_payee(payee_name)
    domain = normalized.replace(" ", "").lower()
    if not (_MIN_DOMAIN_LENGTH <= len(domain) <= _MAX_DOMAIN_LENGTH):
        return None
    return f"{domain}.com"


@dataclass(frozen=True)
class Icon:
    """A resolved icon, addressable either way a client wants it."""

    key: str

    @property
    def url(self) -> str:
        return icon_url(self.key)

    @property
    def b64(self) -> str | None:
        return icon_bytes(self.key)


async def payee_icons(
    db: AsyncSession, payees: Iterable[tuple[int | None, str]]
) -> dict[str, Icon]:
    """``{name: Icon}`` for rows that know their payee by id and name (a
    transaction's merchant, a stream's payee, the directory itself): the
    stored payee's logo or website is the authoritative key, then the
    resolver guesses. One lookup per batch, never per row."""
    from app.services.finance.domains.ledger.merchants import merchant_icon_sources

    pairs = list(payees)
    sources = await merchant_icon_sources(
        db, {mid for mid, _ in pairs if mid is not None}
    )
    overrides = {name: sources[mid] for mid, name in pairs if mid in sources}
    keys = await resolve_icon_keys(db, [name for _, name in pairs], overrides)
    return {name: Icon(key) for name, key in keys.items()}


async def payee_icons_by_name(
    db: AsyncSession, names: list[str | None], *, owner_user_id: int | None = None
) -> dict[str, Icon]:
    """``payee_icons`` for a surface that has only names (the overview
    cards, the projected ledger): a name that is a stored payee still
    gets that payee's logo or website before the guess."""
    from app.services.finance.domains.ledger.merchants import merchants_by_name

    found = await merchants_by_name(db, names, owner_user_id=owner_user_id)
    return await payee_icons(
        db, [(found[n].id if n in found else None, n) for n in names if n]
    )


async def institution_icons(
    db: AsyncSession, institutions: Iterable[Any]
) -> dict[int, Icon]:
    """``{institution id: Icon}`` for the banks behind a page of
    accounts.

    The same resolution a payee gets, and for the same reason: a stored
    logo or domain is authoritative, and a bank's NAME guesses well where
    an account's name never could ("Fidelity" -> fidelity.com resolves;
    "ROTH IRA" -> rothira.com is nonsense). That asymmetry is why an
    account borrows its mark from its institution rather than from
    itself.
    """
    rows = [i for i in institutions if getattr(i, "id", None) and i.name]
    overrides = {
        i.name: key
        for i in rows
        if (key := getattr(i, "logo_url", None) or getattr(i, "domain", None))
    }
    keys = await resolve_icon_keys(db, [i.name for i in rows], overrides)
    return {i.id: Icon(keys[i.name]) for i in rows if i.name in keys}


def icon_url(key: str) -> str:
    """The same-origin URL that serves a stored icon (see the web
    frontend's ``/icons`` route)."""
    from urllib.parse import urlencode

    return f"{ICON_PATH}?{urlencode({'key': key})}"


def icon_bytes(key: str) -> str | None:
    """The base64 png behind a key that ``resolve_icon_keys`` returned."""
    return _CACHE.get(key)


async def icons_for_names(
    db: AsyncSession,
    names: list[str | None],
    domains_by_name: dict[str, str] | None = None,
) -> dict[str, str]:
    """``{payee name: base64 png}`` for whichever names resolve NOW.

    Memory first, then stored ``finance_icon`` rows. Keys neither layer
    knows (or whose negative entry has aged out) are scheduled for a
    background fill and simply absent from this response - the caller's
    page renders its fallback once and finds the icon on the next load.
    """
    keys = await resolve_icon_keys(db, names, domains_by_name)
    return {name: _CACHE[key] for name, key in keys.items() if _CACHE.get(key)}


async def resolve_icon_keys(
    db: AsyncSession,
    names: list[str | None],
    domains_by_name: dict[str, str] | None,
) -> dict[str, str]:
    """``{payee name: stored key}`` for the names whose icon is in memory
    after this call; the rest are scheduled for a fill (see
    ``icons_for_names``). ``domains_by_name`` carries each name's
    authoritative key (a logo URL or a real domain) and beats the guess."""
    overrides = domains_by_name or {}
    wanted: dict[str, str] = {}  # name -> key
    for name in names:
        if not name or name in wanted:
            continue
        # An explicit key always wins over the guess.
        domain = overrides.get(name) or merchant_icon_domain(name)
        if domain is not None and len(domain) <= _MAX_KEY_LENGTH:
            wanted[name] = domain

    unknown = sorted({d for d in wanted.values() if d not in _CACHE})
    if unknown:
        stored = await queries.icons_by_domains(db, unknown)
        now = utcnow()
        to_fetch: list[str] = []
        for domain in unknown:
            row = stored.get(domain)
            if row is None:
                to_fetch.append(domain)
            elif row.icon_b64 is None and now - row.fetched_at > _NEGATIVE_RETRY:
                to_fetch.append(domain)
            else:
                _remember(domain, row.icon_b64)
        if to_fetch:
            _schedule_fill(to_fetch)

    return {name: key for name, key in wanted.items() if _CACHE.get(key)}


def _remember(domain: str, icon_b64: str | None) -> None:
    if len(_CACHE) < _CACHE_MAX:
        _CACHE[domain] = icon_b64


def _schedule_fill(domains: list[str]) -> None:
    """Kick off a fire-and-forget fill for ``domains``, skipping any a
    running fill already covers. Never awaited by a request."""
    fresh = [d for d in domains if d not in _IN_FLIGHT]
    if not fresh:
        return
    _IN_FLIGHT.update(fresh)
    task = asyncio.create_task(_fill_icons(fresh))
    # A render-path decoration is never worth an "exception was never
    # retrieved" warning; failures were already logged/stored as misses.
    task.add_done_callback(lambda t: t.cancelled() or t.exception())


async def _fill_icons(domains: list[str]) -> None:
    """Fetch ``domains`` upstream and persist every answer - bytes or a
    negative row - in its own session (the requesting session is gone)."""
    from app.core.db import get_async_session

    try:
        fetched = await _fetch_domains(domains)
        async with get_async_session() as db:
            existing = await queries.icons_by_domains(db, fetched.keys())
            now = utcnow()
            for domain, icon_b64 in fetched.items():
                row = existing.get(domain)
                if row is None:
                    db.add(
                        FinanceIcon(domain=domain, icon_b64=icon_b64, fetched_at=now)
                    )
                else:
                    row.icon_b64 = icon_b64
                    row.fetched_at = now
                    db.add(row)
                _remember(domain, icon_b64)
    finally:
        _IN_FLIGHT.difference_update(domains)


def upstream_request(key: str) -> tuple[str, dict[str, Any] | None]:
    """Where a key's bytes come from: a logo URL is fetched as is, a
    domain goes through the favicon service."""
    if key.startswith(("http://", "https://")):
        return key, None
    return UPSTREAM, {"sz": 64, "domain": key}


async def _fetch_domains(domains: list[str]) -> dict[str, str | None]:
    """``{key: base64 png or None}`` fetched concurrently. None means the
    key answered with no usable icon - an answer worth storing, not an
    error."""
    import httpx

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    results: dict[str, str | None] = {}

    async def one(client: httpx.AsyncClient, domain: str) -> None:
        async with semaphore:
            try:
                # follow_redirects: the service answers 301 to its real
                # asset host and httpx (unlike urllib) does not follow by
                # default - without this every icon misses on a redirect
                # it should have chased.
                url, params = upstream_request(domain)
                response = await client.get(url, params=params)
                payload = response.content if response.status_code == 200 else b""
            except Exception:
                # Includes having no network at all. An icon is never
                # worth failing the page it decorates.
                payload = b""
        results[domain] = base64.b64encode(payload).decode() if payload else None

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            await asyncio.gather(*(one(client, d) for d in domains))
    except Exception:
        # No network at all: report every domain as a miss so the negative
        # rows still bound retries.
        return {domain: results.get(domain) for domain in domains}
    return results
