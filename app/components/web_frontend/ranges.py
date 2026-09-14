"""The time windows the range chips offer.

One vocabulary for every chip row in the app. A window is a number of
days; ``ALL`` is the "everything" chip. ``since()`` turns a window into
the date a filter should start at, which is what the ledger pages need;
the summary pages hand their day count to the API instead.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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
    # The same UTC clock the finance service reads. Spelled out rather
    # than imported: the web frontend ships in projects that have no
    # finance service, and a window is the browser's concern anyway.
    return datetime.now(UTC).date() - timedelta(days=days)


def horizon(days: int, cap: int) -> int:
    """A window as a real day count, for the endpoints that take one:
    ``All`` is that endpoint's own ceiling."""
    return cap if days >= ALL else min(days, cap)
