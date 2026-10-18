"""The two days of the month a twice-monthly bill lands on (#338).

Pay on the 15th and the last day is not "every 15 days": stepped by a
fixed count it drifts off both days within a few months, and a median
gap of 15 matches every two weeks first, so the paycheck read as
biweekly or not at all. A semi-monthly bill is its two days instead,
and stepping it means finding the next of them.

Pure: no database, no clock.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from datetime import date

# A day in a month's last three is its last day, whatever the month's
# length: Feb 28, Sep 30 and Oct 31 are one payday, written 31.
MONTH_END = 31
# A payday that falls on a weekend moves a day or two, so a charge this
# close to one of the two days is still that day.
SLACK_DAYS = 3
# Half a month apart, give or take: the 1st and 15th, the 15th and last.
MIN_APART, MAX_APART = 10, 20
# Of the dates, the share that must land on the two days.
MIN_ON_DAYS = 0.75


def _day(when: date) -> int:
    last = calendar.monthrange(when.year, when.month)[1]
    return MONTH_END if when.day > last - SLACK_DAYS else when.day


def pay_days_of(dates: Sequence[date]) -> tuple[int, int] | None:
    """The two days these dates land on, or None when they are not two.

    Each date's day, with the month's last three days as one, grouped
    where they sit within ``SLACK_DAYS`` of each other; the two largest
    groups are the days, named by the day each lands on most. Every two
    weeks drifts through the month and never holds two groups for long.
    """
    if len(dates) < 2:
        return None
    groups: list[list[int]] = []
    for day in sorted(_day(d) for d in dates):
        if groups and day - groups[-1][-1] <= SLACK_DAYS:
            groups[-1].append(day)
        else:
            groups.append([day])
    largest = sorted(groups, key=len, reverse=True)[:2]
    if len(largest) < 2 or sum(map(len, largest)) < MIN_ON_DAYS * len(dates):
        return None
    first, second = sorted(max(sorted(set(g)), key=g.count) for g in largest)
    if not MIN_APART <= second - first <= MAX_APART:
        return None
    return first, second


def _on(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def next_pay_day(day: date, pay_days: Sequence[int]) -> date:
    """The next of ``pay_days`` after ``day``. More than ``SLACK_DAYS``
    after it: a payday paid early on a Friday is that payday, and the
    next one is the other day."""
    for months in range(3):
        index = day.month - 1 + months
        year, month = day.year + index // 12, index % 12 + 1
        for wanted in sorted(pay_days):
            candidate = _on(year, month, wanted)
            if (candidate - day).days > SLACK_DAYS:
                return candidate
    raise ValueError(f"no pay day in {pay_days!r} after {day}")
