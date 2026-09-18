"""How to reach a party: the shape of the ``contact`` block.

Its own module rather than a corner of ``models``, because it is a
SHAPE and a rule rather than a table: the four keys the app asks
questions of, and the labelled lines for everything else a place
prints. Declared once so the form, the list, the pages and Illiana's
contact.create all write the same thing.
"""

from __future__ import annotations

from typing import Any

# The keys the ``contact`` JSON may carry, with their labels. A county
# office has a fax and a person a mobile, so the column stays JSON; the
# FORM does not.
CONTACT_FIELDS = (
    ("address", "Address"),
    ("phone", "Phone"),
    ("email", "Email"),
    # The website is what makes an organization a PLACE: a pension fund
    # is somewhere you log in, and a fact read off its portal wants to
    # point at the org rather than repeat the address every time.
    ("website", "Website"),
)

# Where the reach details nothing has a field for are kept. Chase prints
# three phone numbers and a contact has one phone field, so the rest
# would go in the note, where nobody can read them as numbers. The
# column has always been JSON for exactly this reason.
#
# The rule for what belongs here rather than in a column of its own: if
# the app will ever do arithmetic on it, compare it or sort by it, it
# needs a column. If a person only ever reads it, a labelled line is
# enough. A second phone number is only ever read.
CONTACT_LINES = "also"


def reach_lines(contact: dict[str, Any] | None) -> list[tuple[str, str]]:
    """The labelled reach details, as ``(label, value)``.

    A label with nothing beside it says nothing, and a value nobody
    labelled is a number whose meaning is lost, so a line needs both.
    """
    return [
        (label, value)
        for line in (contact or {}).get(CONTACT_LINES) or []
        if (label := str(line.get("label") or "").strip())
        and (value := str(line.get("value") or "").strip())
    ]
