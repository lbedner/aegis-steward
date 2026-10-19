"""Which accounts make up the household's net worth, and when (#314).

One rule, asked by the line, the headline, the API and the charts, so
none of them can sum a different set of accounts again.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from app.services.finance.utils import current_date


def counts_in_net_worth(account: Any, on: date | None = None) -> bool:
    """Whether ``account`` is part of the household's net worth on ``on``
    (today when None) - the one rule the line, the headline and the API
    all ask (#314). Ours (no subject: a parent's house held here is
    theirs), not hidden (a goal's pot is money counted where it sits),
    not a property somebody marked out of net worth, and not closed by
    then (#315). ``account`` is the row or its response."""
    if account.is_hidden or account.subject_id is not None:
        return False
    if not _property_counts(account):
        return False
    if not account.is_closed:
        return True
    day = on or current_date()
    return account.closed_on is not None and day < account.closed_on


def _property_counts(account: Any) -> bool:
    """A property's own "include in net worth" setting, on the row's
    metadata or the response's property summary."""
    meta = getattr(account, "metadata_", None)
    if meta is not None:
        return bool(meta.get("property_include_in_net_worth", True))
    summary = getattr(account, "property", None)
    return summary is None or summary.include_in_net_worth


def sum_into(day: list[int], classification: str, value: int) -> None:
    """Add one balance to a day's ``[assets, liabilities]``. The second is a
    magnitude OWED, always positive (``net_worth = assets - liabilities``):
    a register-derived card balance is negative, and adding it raw turned
    the subtraction into an addition - net worth too high by twice the
    debt."""
    if classification == "asset":
        day[0] += value
    elif classification == "liability":
        day[1] += abs(value)


def assets_and_owed(balances: Iterable[tuple[str, int]]) -> tuple[int, int]:
    """(assets, owed) over ``(classification, balance)`` pairs, summed as
    the line sums a day: every page's total is the line's figure."""
    day = [0, 0]
    for classification, value in balances:
        sum_into(day, classification, value)
    return day[0], day[1]


def net_of(balances: Iterable[tuple[str, int]]) -> int:
    """Assets less what is owed, over ``(classification, balance)`` pairs."""
    assets, owed = assets_and_owed(balances)
    return assets - owed
