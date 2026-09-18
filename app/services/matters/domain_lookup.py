"""Going from a name to a domain, and refusing to guess.

The model knows that Delta Dental's site is deltadentalins.com. Unlike a
phone number, that guess is cheap to CHECK: fetch it and accept it only
if the organization's name is actually on the page. VERIFICATION IS THE
WHOLE DESIGN. A guess that cannot be confirmed is dropped, not filed
with a hedge, because a hedged field reads as a fact the moment anybody
else looks at the record.

The agent proposes candidate domains from its own knowledge; this module
decides. She never reports an unconfirmed domain, because she is never
told one.

What this is allowed to do, and nothing more:

- HTTPS by default, a short timeout, a small budget of candidates.
- Follow ONE redirect, and only while it stays on the host that was
  asked about. A redirect off-host is a different organization's server
  and its content proves nothing about this name.
- Read TEXT. Nothing is executed, nothing is stored, no cookies, no
  headers carried anywhere.
- Never raise. A dead host, a typo and no network at all are the same
  answer - None - because a lookup is a convenience and must not be an
  outage.
"""

from __future__ import annotations

import re
from typing import Any

# A lookup, not a scan. Forty guesses is forty requests to somebody
# else's servers, and the budget is the whole defence against a helper
# that quietly becomes a crawler.
CANDIDATE_BUDGET = 4
TIMEOUT_SECONDS = 4.0

# Where an organization puts its contact details, in the order worth
# trying. A site that hides them behind a search form is a site this
# gives up on rather than explores.
CONTACT_PATHS = ("/contact", "/contact-us", "/about/contact", "/about", "/about-us")

# What a company calls itself in registration but not in conversation.
_SUFFIXES = re.compile(
    r"\b(?:inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|"
    r"plc|llp|lp|pc|pllc|the)\b\.?",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[^a-z0-9]+")


def _bare(name: str) -> str:
    """A name with the corporate furniture taken off, for comparing.

    "The Delta Dental of New York, Inc." and "DELTA   DENTAL OF NEW YORK"
    are the same organization, and a match that turns on a comma is not
    a match anybody wants to depend on.
    """
    return _SPACE.sub(" ", _SUFFIXES.sub(" ", name or "").casefold()).strip()


def says(page: str, name: str) -> bool:
    """Whether this page claims to belong to this organization.

    Deliberately blunt: the normalized name appears in the normalized
    text. A parked domain answers 200 and says nothing about anybody,
    which is exactly what this refuses.
    """
    wanted = _bare(name)
    if not wanted:
        return False
    return wanted in _SPACE.sub(" ", _TAGS.sub(" ", page or "").casefold())


def _host_of(candidate: str) -> str:
    """The host part of a candidate, however it was written."""
    stripped = re.sub(r"^https?://", "", (candidate or "").strip(), flags=re.I)
    return stripped.split("/")[0].casefold()


async def _read(url: str, host: str) -> str | None:
    """The text at a URL, or None. Follows ONE redirect, on-host only.

    ``follow_redirects=False`` and the hop taken by hand, because the
    rule is not "how many" but "where to": a 301 onto another company's
    server would otherwise be fetched and its page read as evidence
    about this name.
    """
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            response = await client.get(url)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if not location or _host_of(location) not in ("", host):
                    return None
                response = await client.get(str(response.url.join(location)))
            if response.status_code != 200:
                return None
            # Text only. A PDF or an image proves nothing and costs the
            # same timeout to download.
            if "text" not in response.headers.get("content-type", "text/html"):
                return None
            return response.text
    except Exception:
        # A dead host, a typo, no network: all the same answer. A lookup
        # is a convenience and must never be an outage.
        return None


async def confirm(
    name: str, candidates: list[str], *, scheme: str = "https"
) -> str | None:
    """The first candidate domain whose page carries this name, or None.

    Tried in the order given, because the caller's first guess is its
    best one. At most ``CANDIDATE_BUDGET`` of them are fetched: a longer
    list is a scan, and the extra guesses are the least likely anyway.
    """
    for candidate in candidates[:CANDIDATE_BUDGET]:
        host = _host_of(candidate)
        if not host:
            continue
        page = await _read(f"{scheme}://{candidate.lstrip('/')}", host)
        if page and says(page, name):
            return candidate
    return None


async def contact_page(
    domain: str, *, scheme: str = "https", paths: tuple[str, ...] = CONTACT_PATHS
) -> list[dict[str, Any]]:
    """The reach fields printed on an organization's contact page.

    The same conservative reader the letterhead lookup uses, so a value
    means the same thing whichever source it came from - and every row
    carries the URL it was read at. A field with no citation is not a
    row.
    """
    from app.services.matters.lookup import found_in, lines_in

    host = _host_of(domain)
    for path in paths:
        url = f"{scheme}://{host}{path}"
        page = await _read(url, host)
        if not page:
            continue
        text = _TAGS.sub("\n", page)
        found = found_in(text)
        rows: list[dict[str, Any]] = [
            {"field": field, "value": value, "url": url}
            for field, value in found.items()
        ]
        rows.extend(
            {"field": "also", "label": label, "value": value, "url": url}
            for label, value in lines_in(text, besides=set(found.values()))
        )
        if rows:
            return rows
    return []
