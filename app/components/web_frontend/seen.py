"""What this reader has already looked at, per surface.

A cookie, not a table. "I have not seen this" is a fact about a person
at a browser, which is what a cookie already is - and it arrives with
every request, so the server can mark the rows it is about to render
rather than shipping the whole ledger to the client to decide.

The rule that makes the mark useful is that it does NOT clear the
instant you arrive. Seeing a list is not reading it, and a highlight
that vanishes on a refresh is a highlight you cannot come back to. So
the watermark advances only after a gap: refresh all you like and the
marks stay; come back later and they are gone, because you looked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from starlette.requests import Request
from starlette.responses import Response

# How long away counts as "came back later" rather than "still reading".
AWAY = timedelta(minutes=30)
# A year: the watermark is a convenience, and losing it only means one
# visit's worth of rows read as new again.
KEEP = 365 * 24 * 60 * 60


def _name(surface: str) -> str:
    return f"seen_{surface}"


def _parse(raw: str | None) -> tuple[datetime | None, datetime | None]:
    """``"<seen>|<touched>"`` -> the two stamps, forgiving anything else."""
    if not raw or "|" not in raw:
        return None, None
    seen, _, touched = raw.partition("|")
    try:
        return datetime.fromisoformat(seen), datetime.fromisoformat(touched)
    except ValueError:
        return None, None


def watermark(request: Request, surface: str) -> datetime | None:
    """What this reader had already seen when they arrived.

    ``None`` on a first visit, which means nothing is marked rather than
    everything: a ledger that lights up entirely on the first look has
    told the reader nothing.
    """
    seen, _touched = _parse(request.cookies.get(_name(surface)))
    return seen


def remember(
    request: Request, response: Response, surface: str, now: datetime | None = None
) -> Response:
    """Record this visit, advancing the watermark only after a gap."""
    now = now or datetime.now(UTC)
    seen, touched = _parse(request.cookies.get(_name(surface)))
    if touched is None:
        seen = now
    elif now - touched > AWAY:
        # Away long enough that the last visit counts as read.
        seen = touched
    response.set_cookie(
        _name(surface),
        f"{(seen or now).isoformat()}|{now.isoformat()}",
        max_age=KEEP,
        httponly=True,
        samesite="lax",
    )
    return response
