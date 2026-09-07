"""The sections of the app, in sidebar order.

``NAV`` is the single source for the sidebar links and the section routes:
``components/sidebar.html`` loops it, and each module under
``routes/finance/`` looks its section up here for the page title and
heading. Add a section by adding an entry and a route module; the tests in
``tests/web/test_sections.py`` fail until both exist.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    path: str
    # Heroicons 24px outline path data, drawn by the ``nav_item`` macro.
    icon: str


NAV: tuple[Section, ...] = (
    Section(
        "overview",
        "Overview",
        "/overview",
        "M2.25 12l8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125"
        "c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125"
        "h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125"
        "V9.75M8.25 21h8.25",
    ),
    Section(
        "accounts",
        "Accounts",
        "/accounts",
        "M2.25 18.75a60.07 60.07 0 0115.797 2.101c.727.198 1.453-.342 1.453-1.096"
        "V18.75M3.75 4.5v.75A.75.75 0 013 6h-.75m0 0v-.375c0-.621.504-1.125"
        "1.125-1.125H20.25M2.25 6v9m18-10.5v.75c0 .414.336.75.75.75h.75m-1.5-1.5"
        "h.375c.621 0 1.125.504 1.125 1.125v9.75c0 .621-.504 1.125-1.125 1.125"
        "h-.375m1.5-1.5H21a.75.75 0 00-.75.75v.75m0 0H3.75m0 0h-.375a1.125 1.125"
        " 0 01-1.125-1.125V15m1.5 1.5v-.75A.75.75 0 003 15h-.75M15 10.5a3 3 0"
        " 11-6 0 3 3 0 016 0zm3 0h.008v.008H18V10.5zm-12 0h.008v.008H6V10.5z",
    ),
    Section(
        "bills",
        "Bills & Income",
        "/bills",
        "M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5",
    ),
    Section(
        "projected",
        "Projected",
        "/projected",
        "M2.25 18L9 11.25l4.306 4.307a11.95 11.95 0 015.814-5.519l2.74-1.22"
        "m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941",
    ),
    Section(
        "budget",
        "Budget",
        "/budget",
        "M10.5 6a7.5 7.5 0 107.5 7.5h-7.5V6zM13.5 10.5H21A7.5 7.5 0 0013.5 3v7.5z",
    ),
    Section(
        "review",
        "Review",
        "/review",
        "M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
    ),
    Section(
        "settings",
        "Settings",
        "/settings",
        "M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5"
        "m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5"
        "m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75",
    ),
)


def section(key: str) -> Section:
    """The ``NAV`` entry for ``key``; a typo is a startup error, not a 404."""
    for entry in NAV:
        if entry.key == key:
            return entry
    raise KeyError(f"no section {key!r} in NAV")
