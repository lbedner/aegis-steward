"""The time windows the range chips offer.

One vocabulary for every chip row in the app. A window is a number of
days; ``ALL`` is the "everything" chip. ``since()`` turns a window into
the date a filter should start at, which is what the ledger pages need;
the summary pages hand their day count to the API instead.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import Depends, Query, Request, Response
from pydantic import BeforeValidator

from app.core.time import today


def _blank_is_none(value: Any) -> Any:
    """A submitted form sends ``""`` for an untouched select or date input."""
    return None if value == "" else value


Blank = BeforeValidator(_blank_is_none)
# A picked from-to range, as every page's query string spells it; an
# explicit ``from`` beats the chips (``horizon``, ``since``).
FROM, TO = "from", "to"
FromDate = Annotated[date | None, Blank, Query(alias=FROM)]
ToDate = Annotated[date | None, Blank, Query(alias=TO)]


# The last range this browser picked, so a visit that names none (the
# sidebar) opens on it. A cookie rather than browser storage: the server
# renders the remembered range on the first paint, either render path.
RANGE_COOKIE = "picked_range"
RANGE_KEEP_SECONDS = 60 * 60 * 24 * 365


def remembered(
    request: Request, start: date | None, end: date | None
) -> tuple[date | None, date | None]:
    """The range to show: the URL's when it names one (blank is a clear),
    else the one this browser picked last."""
    if FROM in request.query_params or TO in request.query_params:
        return start, end
    saved_start, _, saved_end = request.cookies.get(RANGE_COOKIE, "").partition("|")
    return _day(saved_start), _day(saved_end)


def _picked(
    request: Request, start: FromDate = None, end: ToDate = None
) -> tuple[date | None, date | None]:
    return remembered(request, start, end)


# A page's from-to range, remembered: the parameters and the cookie in one
# declaration. The page hands the same pair to ``remember`` on the way out.
PickedRange = Annotated[tuple[date | None, date | None], Depends(_picked)]


def remember(response: Response, start: date | None, end: date | None) -> Response:
    """Keep the shown range for this browser's next visit, or forget it."""
    if start is None and end is None:
        response.delete_cookie(RANGE_COOKIE)
    else:
        value = f"{start.isoformat() if start else ''}|{end.isoformat() if end else ''}"
        response.set_cookie(
            RANGE_COOKIE,
            value,
            max_age=RANGE_KEEP_SECONDS,
            samesite="lax",
            httponly=True,
        )
    return response


def _day(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def date_params(start: date | None, end: date | None) -> dict[str, str]:
    """A picked range as query parameters; nothing for an unpicked end."""
    picked = {FROM: start, TO: end}
    return {name: day.isoformat() for name, day in picked.items() if day is not None}


# Big enough to mean "no cutoff", small enough to stay a plain int in a
# query string. The API's own windows cap at 3650 days, so a summary
# page's "All" is that ceiling rather than this.
ALL = 9999

# One set of chips, everywhere. The pages differ in what a window means
# (how far back the ledger reads, how far forward the forecast runs), not
# in what it is called, so the row reads the same wherever it appears.
WINDOWS: tuple[tuple[int, str], ...] = (
    (1, "1d"),
    (7, "7d"),
    (14, "14d"),
    (30, "1m"),
    (90, "3m"),
    (365, "1y"),
    (ALL, "All"),
)


# The same chips at a different scale. A ledger is read in days and a
# house is held in decades, so a value history offers years - but it is
# the same row, the same field and the same ``since()``, because a second
# way to say "how far back" is a second thing to keep in step.
LONG_WINDOWS: tuple[tuple[int, str], ...] = (
    (365, "1y"),
    (1825, "5y"),
    (3650, "10y"),
    (ALL, "All"),
)


# Two windows that are not a number of days back from today, but a
# position in this asset's own story: everything up to the day it was
# bought, and everything after. Negative so they cannot be confused with
# a day count, and offered only where a purchase is actually known.
BEFORE_PURCHASE = -1
SINCE_PURCHASE = -2
PURCHASE_WINDOWS: tuple[tuple[int, str], ...] = (
    (BEFORE_PURCHASE, "Before I bought"),
    (SINCE_PURCHASE, "Since I bought"),
)


def relative_to_purchase(days: int) -> bool:
    """Whether this window is measured from the purchase, not from today."""
    return days in {BEFORE_PURCHASE, SINCE_PURCHASE}


def since(days: int | None) -> date | None:
    """The date a window starts at; ``None`` when it means everything."""
    if days is None or days >= ALL:
        return None
    return today() - timedelta(days=days)


def horizon(days: int, cap: int, start: date | None = None) -> int:
    """A window as a real day count, for the endpoints that take one:
    ``All`` is that endpoint's own ceiling. A picked ``start`` (a from-to
    range, #342) beats the chips: the days from it to today."""
    if start is not None:
        return max(1, min(cap, (today() - start).days + 1))
    return cap if days >= ALL else min(days, cap)
