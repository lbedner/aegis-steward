"""How to reach a party: the shape of the ``contact`` block.

Its own module rather than a corner of ``models``, because it is a
SHAPE and a rule rather than a table: the four keys the app asks
questions of, and the labelled lines for everything else a place
prints. Declared once so the form, the list, the pages and Illiana's
contact.create all write the same thing.
"""

from __future__ import annotations

import re
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


# WHAT ALREADY HAS A HOME DOES NOT GET A SECOND ONE HERE.
#
# A routing number is not a way to reach anybody. #179 decided it
# belongs to the BANK - ``finance_institution.routing_number``, set
# through the ``account.institution`` change type - because every
# account held there shares the one. Illiana read 221979363 off a credit
# union's contact page and filed it as a labelled line; told that off,
# she left the lines clean and wrote the number into the NOTE in a
# sentence, which is the same second home in prose. Rejecting the card
# alone changed nothing either time: nothing in the code knew what the
# number was, so the next proposal was the last one (2026-09-18).
#
# The same card repeated the main phone number under a label of its own
# ("International calls"). Two copies of one number is one copy somebody
# will correct and one they will not.
_NOT_A_WAY_IN = frozenset({"routing", "aba", "rtn"})

# Written out, no separators, the way every bank prints it.
_NINE_DIGITS = re.compile(r"\b\d{9}\b")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _belongs_to_the_bank(where: str) -> str:
    return (
        f"{where} reads as a routing number, which belongs to the bank rather "
        "than to a contact: send it as routing_number on an "
        "account.institution card, where every account held there shares the "
        "one."
    )


def one_home(contact: dict[str, Any], note: str | None = None) -> None:
    """Raise when a card would write down something that already lives
    somewhere else: the bank's routing number, or a labelled line
    repeating the field above it.

    Both contact payloads call this, because a rule enforced on the
    creating card and not the amending one is a rule with a way round.

    It can only see what the CARD says, so an amendment adding a line
    that repeats a phone number it did not resend gets through. The
    executor is not the place for it: a refusal has to happen while
    there is still a card to fix.
    """
    named = {
        label: value.strip()
        for key, label in CONTACT_FIELDS
        if (value := str(contact.get(key) or ""))
    }
    for line_label, value in reach_lines(contact):
        if _NOT_A_WAY_IN & _words(line_label):
            raise ValueError(_belongs_to_the_bank(f'"{line_label}"'))
        for field_label, already in named.items():
            if value == already:
                raise ValueError(
                    f'"{line_label}" is the {field_label} again, word for '
                    f"word. Give the line something the {field_label} does "
                    "not already say, or leave it off."
                )
    if note and _NOT_A_WAY_IN & _words(note) and _NINE_DIGITS.search(note):
        raise ValueError(_belongs_to_the_bank("The note"))
