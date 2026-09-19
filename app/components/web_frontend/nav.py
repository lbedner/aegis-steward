"""The sections of the app, in sidebar order.

``NAV`` is the single source for the sidebar links and the section routes:
``components/sidebar.html`` loops it, and each module under
``routes/finance/`` looks its section up here for the page title and
heading. Add a section by adding an entry and a route module; the tests in
``tests/web/test_sections.py`` fail until both exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    path: str
    # Heroicons 24px outline path data, drawn by the ``nav_item`` macro.
    icon: str
    # What this section is ABOUT, drawn as a heading above the first
    # section that carries it. Declared here rather than in the sidebar
    # so the grouping and the order are one list: a heading written in
    # the template would be a second place the nav is decided, and the
    # two would disagree the first time a section moved.
    #
    # ``None`` closes the previous group without opening one - the
    # sections nobody thinks of as belonging to a subject (Chat,
    # Settings) sit under a rule at the foot instead.
    group: str | None = None
    # A partial the nav fetches for a mark beside the label (an overdue
    # deadline), or None for sections with nothing to flag.
    attention: str | None = None


NAV: tuple[Section, ...] = (
    Section(
        "overview",
        "Overview",
        "/overview",
        "M2.25 12l8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125"
        "c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125"
        "h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125"
        "V9.75M8.25 21h8.25",
        group="Money",
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
        group="Money",
    ),
    Section(
        "bills",
        "Bills & Income",
        "/bills",
        "M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5",
        group="Money",
    ),
    Section(
        "projected",
        "Projected",
        "/projected",
        "M2.25 18L9 11.25l4.306 4.307a11.95 11.95 0 015.814-5.519l2.74-1.22"
        "m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941",
        group="Money",
    ),
    Section(
        "budget",
        "Budget",
        "/budget",
        "M10.5 6a7.5 7.5 0 107.5 7.5h-7.5V6zM13.5 10.5H21A7.5 7.5 0 0013.5 3v7.5z",
        group="Money",
    ),
    Section(
        "review",
        "Review",
        "/review",
        "M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
        group="Money",
    ),
    Section(
        "matters",
        "Matters",
        "/matters",
        "M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125"
        "v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125"
        "1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125"
        "V11.25a9 9 0 00-9-9z",
        group="Records",
        attention="/matters/attention",
    ),
    Section(
        "contacts",
        "Contacts",
        "/contacts",
        # Heroicon: users
        "M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 "
        "00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 "
        "12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 "
        "0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 "
        "2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z",
        group="Records",
    ),
    Section(
        "documents",
        "Documents",
        "/documents",
        # Heroicon: document-duplicate
        "M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 "
        "01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 "
        "011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46"
        "-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504"
        "-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m12 "
        "6.625v-1.875a3.375 3.375 0 00-3.375-3.375h-1.5a1.125 1.125 0 01-1.125"
        "-1.125v-1.5a3.375 3.375 0 00-3.375-3.375H9.75",
        group="Records",
    ),
    Section(
        "chat",
        "Chat",
        "/chat",
        "M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193"
        "-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163"
        "a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095"
        "48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286"
        "c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026"
        "-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402"
        "-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235"
        ".577.075 1.157.14 1.74.194V21l4.155-4.155",
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


# The Settings sub-nav. Here rather than in the settings routes because
# it IS navigation, and because a module at its recorded size cannot
# grow a tab - the budget decided where this lives, which is what having
# one is for.
SETTINGS_TABS: tuple[tuple[str, str, str], ...] = (
    ("connections", "Connections", ""),
    ("categories", "Categories", "/categories"),
    ("payees", "Payees", "/payees"),
    ("institutions", "Institutions", "/institutions"),
    ("comms", "Comms", "/comms"),
    ("activity", "Activity", "/activity"),
)


def settings_nav(current: str) -> dict[str, Any]:
    """The Settings sub-nav context, for whichever module owns a tab."""
    settings = section("settings")
    return {
        "nav_id": "settings-nav",
        "nav_label": "Settings sections",
        "sub_nav": [
            {"key": key, "label": label, "href": settings.path + suffix, "count": 0}
            for key, label, suffix in SETTINGS_TABS
        ],
        "current_tab": current,
    }


def matter_tabs(matter_id: int, current: str, outstanding: int = 0) -> dict[str, Any]:
    """A matter's three faces, through the sub-nav every section uses.

    The case is where the work happens; the timeline is that same case
    read back as a sequence; the answer sheet is what leaves the
    building. Two pages rather than one because the sheet is read
    beside a paper form, with the app's own chrome dropped out of print,
    and a page that is both is a page that prints badly.

    ``outstanding`` on the tab, because the useful thing to know before
    clicking is whether anything is still missing.
    """
    base = f"{section('matters').path}/{matter_id}"
    return {
        "nav_label": "Matter",
        "nav_id": "matter-tabs",
        "current_tab": current,
        "sub_nav": [
            {"key": "case", "label": "The case", "href": base},
            {"key": "timeline", "label": "Timeline", "href": f"{base}/timeline"},
            {
                "key": "answers",
                "label": "Answer sheet",
                "href": f"{base}/answers",
                "count": outstanding,
            },
        ],
    }


def account_tabs(account_id: int, current: str, filed: int = 0) -> dict[str, Any]:
    """An account's two faces, as the sub-nav every section uses.

    The register answers "what happened here", the cover sheet answers
    "what IS this", and Documents is the paper both of them are read
    against. One page could not do all three without the facts
    scrolling away above a thousand rows.

    ``filed`` puts the count on the tab, because the useful thing to
    know about an account's paper before you click is whether there is
    any.
    """
    base = f"{section('accounts').path}/{account_id}"
    return {
        "nav_label": "Account",
        "nav_id": "account-tabs",
        "current_tab": current,
        "sub_nav": [
            {"key": "cover", "label": "Overview", "href": f"{base}/overview"},
            {
                "key": "documents",
                "label": "Documents",
                "href": f"{base}/documents",
                "count": filed,
            },
            {"key": "register", "label": "Register", "href": base},
        ],
    }
